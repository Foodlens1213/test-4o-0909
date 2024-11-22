from flask import Flask, request, abort, jsonify, render_template
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage, ImageMessage, FlexSendMessage, PostbackEvent
import os
import io
from dotenv import load_dotenv
from firebase_service import initialize_firebase, save_recipe_to_db, get_recipe_from_db, get_user_favorites, delete_favorite_from_db
from google_vision_service import detect_labels
from chatgpt_service import translate_and_filter_ingredients, generate_recipe_response
import json
import re

# 載入環境變數
load_dotenv()
app = Flask(__name__)

# 初始化 Firebase
db = initialize_firebase()

# LINE Bot API 和 Webhook 設定
line_bot_api = LineBotApi(os.getenv("LINE_CHANNEL_ACCESS_TOKEN"))
handler = WebhookHandler(os.getenv("LINE_CHANNEL_SECRET"))

# 儲存處理後的食材資料（供後續使用）
user_ingredients = {}


# 錯誤處理函數
def handle_error(event, message="發生錯誤，請稍後再試"):
    print(f"錯誤詳情: {message}")
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=message)
    )


# 提取料理類型和菜數
def parse_user_message(user_message):
    match = re.search(r"做(.*?)(?:幾|道)?", user_message)
    dish_type = match.group(1).strip() if match else "料理"

    dish_count = None
    if re.search(r"\d+", user_message):
        dish_count = int(re.search(r"\d+", user_message).group())
    else:
        dish_count = chinese_to_digit(user_message)

    return dish_type, dish_count if dish_count else 1


# 將中文數字轉換為阿拉伯數字
def chinese_to_digit(user_message):
    chinese_digits = {'零': 0, '一': 1, '二': 2, '兩': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7, '八': 8, '九': 9}
    match = re.search(r"[零一二兩三四五六七八九]", user_message)
    return chinese_digits[match.group()] if match else None


# 問使用者料理需求
def ask_user_for_recipe_info():
    return "您今天想做甚麼樣的料理？幾道菜？"


# 生成多道食譜
def generate_multiple_recipes(dish_count, dish_type, ingredients):
    recipes = []
    existing_dishes = set()

    for i in range(dish_count):
        while True:
            specific_message = f"第 {i + 1} 道 {dish_type} 料理"
            dish_name, ingredient_text, recipe_text = generate_recipe_response(specific_message, 1, ingredients)

            if dish_name not in existing_dishes:
                recipes.append((dish_name, ingredient_text, recipe_text))
                existing_dishes.add(dish_name)
                break
            else:
                print("生成的食譜重複，重新生成...")

    return recipes


# 創建 Flex Message
def create_flex_message(recipe_text, user_id, dish_name, ingredient_text, ingredients, recipe_number):
    recipe_id = save_recipe_to_db(db, user_id, dish_name, recipe_text, ingredient_text)
    button_data_new_recipe = json.dumps({
        "action": "new_recipe",
        "user_id": user_id,
        "ingredients": ingredients
    })
    button_data_save_favorite = json.dumps({
        "action": "save_favorite",
        "recipe_id": recipe_id
    })

    return {
        "type": "bubble",
        "body": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {"type": "text", "text": f"料理名稱 {recipe_number}：{dish_name}", "wrap": True, "weight": "bold", "size": "xl"},
                {"type": "text", "text": f"食材：{ingredient_text}", "wrap": True, "margin": "md", "size": "sm"},
                {"type": "text", "text": "食譜內容：", "wrap": True, "weight": "bold", "size": "lg", "margin": "md"},
                {"type": "text", "text": recipe_text if recipe_text else "食譜內容缺失", "wrap": True, "margin": "md", "size": "sm"}
            ]
        },
        "footer": {
            "type": "box",
            "layout": "vertical",
            "spacing": "sm",
            "contents": [
                {"type": "button", "action": {"type": "postback", "label": "有沒有其他的食譜", "data": button_data_new_recipe},
                 "color": "#474242", "style": "primary", "height": "sm"},
                {"type": "button", "action": {"type": "postback", "label": "把這個食譜加入我的最愛", "data": button_data_save_favorite},
                 "color": "#474242", "style": "primary", "height": "sm"}
            ]
        }
    }


# 處理圖片訊息
@handler.add(MessageEvent, message=ImageMessage)
def handle_image_message(event):
    try:
        message_id = event.message.id
        message_content = line_bot_api.get_message_content(message_id)
        image_data = io.BytesIO(message_content.content)
        detected_labels = detect_labels(image_data.read())

        if detected_labels:
            processed_text = translate_and_filter_ingredients(detected_labels)
            user_id = event.source.user_id
            if processed_text:
                user_ingredients[user_id] = processed_text
                question_response = ask_user_for_recipe_info()
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text=question_response))
            else:
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="未能識別出任何食材。"))
        else:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="無法辨識出任何物體。"))
    except Exception as e:
        handle_error(event, str(e))


# 處理使用者訊息
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    try:
        user_id = event.source.user_id
        user_message = event.message.text
        dish_type, dish_count = parse_user_message(user_message)
        ingredients = user_ingredients.get(user_id)

        if ingredients:
            recipes = generate_multiple_recipes(dish_count, dish_type, ingredients)
            flex_bubbles = [
                create_flex_message(recipe_text, user_id, dish_name, ingredient_text, ingredients, i + 1)
                for i, (dish_name, ingredient_text, recipe_text) in enumerate(recipes)
            ]
            carousel = {"type": "carousel", "contents": flex_bubbles}
            line_bot_api.reply_message(event.reply_token, FlexSendMessage(alt_text="您的多道食譜", contents=carousel))
        else:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="請先上傳圖片來辨識食材。"))
    except Exception as e:
        handle_error(event, str(e))


# 處理 Postback 事件
@handler.add(PostbackEvent)
def handle_postback(event):
    try:
        data = json.loads(event.postback.data)
        action = data.get('action')
        user_id = data.get('user_id')

        if action == 'new_recipe':
            ingredients = data.get('ingredients')
            recipes = generate_multiple_recipes(1, "新的食譜", ingredients)
            if recipes:
                flex_message = FlexSendMessage(
                    alt_text="您的新食譜",
                    contents=create_flex_message(recipes[0][2], user_id, recipes[0][0], recipes[0][1], ingredients, 1)
                )
                line_bot_api.push_message(user_id, flex_message)
        elif action == 'save_favorite':
            recipe_id = data.get('recipe_id')
            recipe = get_recipe_from_db(db, recipe_id)
            if recipe:
                db.collection('favorites').add({
                    'user_id': user_id,
                    'dish': recipe['dish'],
                    'ingredient': recipe['ingredient'],
                    'recipe': recipe['recipe'],
                    'recipe_id': recipe_id
                })
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="已成功將食譜加入我的最愛!"))
    except Exception as e:
        handle_error(event, str(e))


# 健康檢查
@app.route("/health", methods=["GET"])
def health_check():
    return "OK", 200


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
