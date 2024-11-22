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

# 載入環境變數
load_dotenv()
app = Flask(__name__)

# 初始化 Firebase
db = initialize_firebase()

# LINE Bot API 和 Webhook 設定
line_bot_api = LineBotApi(os.getenv("LINE_CHANNEL_ACCESS_TOKEN"))
handler = WebhookHandler(os.getenv("LINE_CHANNEL_SECRET"))

# 處理圖片訊息，進行 Google Cloud Vision 的物體偵測（Label Detection）
@handler.add(MessageEvent, message=ImageMessage)
def handle_image_message(event):
    message_id = event.message.id
    message_content = line_bot_api.get_message_content(message_id)

    # 讀取圖片內容
    image_data = io.BytesIO(message_content.content)

    # 使用封裝的 detect_labels 方法
    detected_labels = detect_labels(image_data.read())

    if detected_labels:
        print(f"辨識到的食材: {detected_labels}")  # 在 log 中顯示食材
        processed_text = translate_and_filter_ingredients(detected_labels)
        user_id = event.source.user_id
        if processed_text:
            user_ingredients[user_id] = processed_text
            question_response = ask_user_for_recipe_info()
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=question_response)
            )
        else:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="未能識別出任何食材，請嘗試上傳另一張清晰的圖片。")
            )
    else:
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="無法辨識出任何物體，請確保圖片中的食材明顯可見。")
        )

# 儲存處理後的食材資料（供後續使用）
user_ingredients = {}

# 問使用者料理需求
def ask_user_for_recipe_info():
    return "您今天想做甚麼樣的料理？幾道菜？"

import re
def clean_text(text):
    # 去除無效字符和表情符號
    return re.sub(r'[^\w\s,.!?]', '', text)
def create_flex_message(recipe_text, user_id, dish_name, ingredient_text, ingredients, recipe_number):
    recipe_id = save_recipe_to_db(db, user_id, dish_name, recipe_text, ingredient_text)
    if isinstance(ingredients, list):
        ingredients_str = ','.join(ingredients)
    else:
        ingredients_str = str(ingredients)
    # 設置 Flex Message 結構
    bubble = {
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
                {"type": "button", "action": {"type": "postback", "label": "有沒有其他的食譜", "data":f"action=new_recipe&user_id={user_id}&ingredients={ingredients_str}"},
                 "color": "#474242", "style": "primary", "height": "sm"},
                {"type": "button", "action": {"type": "postback", "label": "把這個食譜加入我的最愛", "data":f"action=save_favorite&recipe_id={recipe_id}"},
                 "color": "#474242", "style": "primary", "height": "sm"}
            ]
        }
    }
    return bubble

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

# 處理使用者需求
@handler.add(PostbackEvent)
def handle_postback(event):
    data = event.postback.data
    params = dict(x.split('=') for x in data.split('&'))
    action = params.get('action')
    user_id = params.get('user_id')
    # 修改 handle_postback 函數中的 `new_recipe` 行動回應
    if action == 'new_recipe':
        # 回覆"沒問題，請稍後~"
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="沒問題，請稍後~")
        )
        ingredients = params.get('ingredients')
        ingredients = ingredients_str.split(',') if ingredients_str else []
        dish_name, ingredient_text, recipe_text = generate_recipe_response("新的食譜", ingredients)
        if dish_name and recipe_text:
            flex_message = FlexSendMessage(
                alt_text="您的新食譜",
                contents=create_flex_message(recipe_text, user_id, dish_name, ingredient_text, ingredients, 1)
            )
            line_bot_api.push_message(user_id, flex_message)
        else:
            line_bot_api.push_message(
                user_id,
                TextSendMessage(text="生成食譜失敗，請稍後再試。")
            )
    elif action == 'save_favorite':
        recipe_id = params.get('recipe_id')    
        user_id = user_id or event.source.user_id  # 確保 user_id 不為 null
        recipe = get_recipe_from_db(db, recipe_id)
        if recipe:
            # 將該食譜儲存在 favorites 集合中
            try:
                db.collection('favorites').add({
                    'user_id': user_id,  # 確保此處使用了正確的 user_id
                    'dish': recipe['dish'],
                    'ingredient': recipe['ingredient'],
                    'recipe': recipe['recipe'],
                    'recipe_id': recipe_id
                })
                line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(text="已成功將食譜加入我的最愛!")
                )
            except Exception as e:
                line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(text="抱歉，儲存過程中發生錯誤。")
                )
        else:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="找不到該食譜，無法加入我的最愛")
            )
def generate_multiple_recipes(dish_count, dish_type, ingredients):
    recipes = []
    existing_dishes = set()  # 用於追踪生成的菜名，避免重複

    for _ in range(dish_count):
        while True:
            # 生成食譜
            dish_name, ingredient_text, recipe_text = generate_recipe_response(dish_type, 1, ingredients)

            # 如果食譜不重複，則加入清單並跳出迴圈
            if dish_name not in existing_dishes:
                recipes.append((dish_name, ingredient_text, recipe_text))
                existing_dishes.add(dish_name)
                break
            else:
                print("生成的食譜重複，重新生成...")
    return recipes

# 將中文數字轉換為阿拉伯數字的函數
def chinese_to_digit(user_message):
    chinese_digits = {'零': 0, '一': 1, '二': 2, '兩': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7, '八': 8, '九': 9}
    chinese_num = re.search(r"[零一二兩三四五六七八九]", user_message)
    if chinese_num:
        return chinese_digits[chinese_num.group()]
    return None

# 更新 handle_message 函數
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_id = event.source.user_id
    user_message = event.message.text
    # 解析使用者訊息
    dish_type, dish_count = parse_user_message(user_message)
    ingredients = user_ingredients.get(user_id, None)
    if ingredients:
        # 生成多道食譜
        recipes = generate_multiple_recipes(dish_count, dish_type, ingredients)
        # 回覆多頁式的食譜 Flex Message
        flex_bubbles = [
            create_flex_message(recipe_text, user_id, dish_name, ingredient_text, ingredients, i + 1)
            for i, (dish_name, ingredient_text, recipe_text) in enumerate(recipes)
        ]
        carousel = {
            "type": "carousel",
            "contents": flex_bubbles
        }
        line_bot_api.reply_message(
            event.reply_token, 
            FlexSendMessage(alt_text="您的多道食譜", contents=carousel)
        )
    else:
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="請先上傳圖片來辨識食材。")
        )
        
# 顯示收藏的食譜（前端頁面）
@app.route('/favorites')
def favorites_page():
    return render_template('favorites.html')
@app.route('/api/favorites', methods=['GET'])
def get_user_favorites_api():
    try:
        # 獲取當前用戶 ID (可根據實際情況使用 LINE ID 或其他登入系統的用戶標識)
        user_id = request.args.get('user_id')  # 前端需要傳遞 user_id 作為參數
        if not user_id:
            return jsonify({'error': 'User ID is required'}), 400
        # 從 Firestore 獲取用戶收藏的食譜
        favorites = get_user_favorites(db, user_id)
        if favorites is not None:
            return jsonify(favorites), 200
        else:
            return jsonify({'error': 'Failed to retrieve favorites'}), 500
    except Exception as e:
        print(f"API 錯誤: {e}")  # 打印詳細日誌
        return jsonify({'error': str(e)}), 500
        
# 使用 `get_recipe_from_db`
def handle_get_recipe(recipe_id):
    recipe = get_recipe_from_db(db, recipe_id)
    if recipe:
        print(f"查詢成功: {recipe}")
    else:
        print("查詢食譜失敗")
        
@app.route('/api/favorites/<recipe_id>', methods=['DELETE'])
def delete_recipe(recipe_id):
    print(f"收到刪除請求，recipe_id: {recipe_id}")
    try:
        if delete_favorite_from_db(db, recipe_id):
            print("食譜成功刪除")
            return jsonify({'message': '食譜已成功刪除！'}), 200
        else:
            print("刪除失敗，找不到對應的食譜")
            return jsonify({'error': '刪除失敗，找不到對應的食譜。'}), 404
    except Exception as e:
        print(f"刪除過程中發生錯誤: {str(e)}")
        return jsonify({'error': f'發生錯誤：{str(e)}'}), 500
    # 直接返回收藏頁面
    favorites = get_user_favorites(db, user_id)  # 替換成實際的 user_id
    return render_template('favorites.html', favorites=favorites, message=message)


# 顯示特定食譜的詳細內容 (供 "查看更多" 使用)
@app.route('/api/favorites/<recipe_id>', methods=['GET'])
def get_recipe_detail(recipe_id):
    try:
        recipe_doc = db.collection('favorites').document(recipe_id).get()
        if recipe_doc.exists:
            return jsonify(recipe_doc.to_dict()), 200
        else:
            return jsonify({'error': 'Recipe not found'}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# Webhook callback 處理 LINE 訊息
@app.route("/callback", methods=["POST"])
def callback():
    body = request.get_data(as_text=True)
    print(f"收到 Webhook 請求: Body: {body}")
    try:
        signature = request.headers["X-Line-Signature"]
        handler.handle(body, signature)
    except InvalidSignatureError:
        print("無效的簽名錯誤!")
        abort(400)
    except Exception as e:
        print(f"發生錯誤: {str(e)}")
        abort(500)
    return "OK"
# 健康檢查路由
@app.route("/health", methods=["GET"])
def health_check():
    return "OK", 200

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
