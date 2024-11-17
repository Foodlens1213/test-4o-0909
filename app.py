from flask import Flask, request, abort, jsonify, render_template
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage, ImageMessage, FlexSendMessage, PostbackEvent
import openai
import os
from google.cloud import vision
from dotenv import load_dotenv
import io
import firebase_admin
from firebase_admin import credentials, firestore


# 載入環境變數
load_dotenv()
app = Flask(__name__)

# 初始化 Firebase Admin SDK 和 Firestore
firebase_credentials_content = os.getenv("FIREBASE_SERVICE_ACCOUNT_KEY")
if firebase_credentials_content:
    firebase_credentials_path = "/tmp/firebase-credentials.json"
    with open(firebase_credentials_path, "w") as f:
        f.write(firebase_credentials_content)
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = firebase_credentials_path

    cred = credentials.Certificate(firebase_credentials_path)
    firebase_admin.initialize_app(cred)
    db = firestore.client()
else:
    print("Firebase 金鑰未正確設置，請檢查環境變數")

# 初始化 Google Cloud Vision API 客戶端
google_credentials_content = os.getenv("GOOGLE_APPLICATION_CREDENTIALS_CONTENT")
if google_credentials_content:
    credentials_path = "/tmp/google-credentials.json"
    with open(credentials_path, "w") as f:
        f.write(google_credentials_content)
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = credentials_path
vision_client = vision.ImageAnnotatorClient()

# LINE Bot API 和 Webhook 設定
line_bot_api = LineBotApi(os.getenv("LINE_CHANNEL_ACCESS_TOKEN"))
handler = WebhookHandler(os.getenv("LINE_CHANNEL_SECRET"))

# OpenAI API 金鑰
openai.api_key = os.getenv("OPENAI_API_KEY")

# 儲存處理後的食材資料（供後續使用）
user_ingredients = {}

# 儲存最愛食譜到 Firebase Firestore
def save_recipe_to_db(user_id, dish_name, recipe_text, ingredient_text):
    try:
        # 手動生成一個新的 DocumentReference，這樣可以提前獲取 ID
        doc_ref = db.collection('recipes').document()
        doc_ref.set({
            'user_id': user_id,
            'dish': dish_name,
            'ingredient': ingredient_text,
            'recipe': recipe_text
        })
        return doc_ref.id  # 返回生成的文檔 ID
    except Exception as e:
        print(f"Firestore 插入錯誤: {e}")
        return None

# 從 Firebase Firestore 根據 recipe_id 查詢食譜
def get_recipe_from_db(recipe_id):
    try:
        recipe_doc = db.collection('recipes').document(recipe_id).get()
        if recipe_doc.exists:
            return recipe_doc.to_dict()
        else:
            print("找不到對應的食譜")
            return None
    except Exception as e:
        print(f"Firestore 查詢錯誤: {e}")
        return None

# 顯示收藏的食譜（前端頁面）
@app.route('/favorites')
def favorites_page():
    return render_template('favorites.html')

# 從 Firestore 獲取用戶的收藏食譜 (API)
@app.route('/api/favorites', methods=['GET'])
def get_user_favorites():
    user_id = request.args.get('user_id')
    if not user_id:
        return jsonify({'error': 'Missing user_id'}), 400

    try:
        # 查詢 favorites 集合，篩選符合 user_id 的食譜
        favorites_ref = db.collection('favorites').where('user_id', '==', user_id)
        docs = favorites_ref.stream()
        favorites = [{'id': doc.id, **doc.to_dict()} for doc in docs]
        return jsonify(favorites), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

def generate_recipe_response(user_message, ingredients, dish_count=0, soup_count=0, cuisine_type=""):
    """
    根據用戶需求生成幾菜幾湯的食譜，並支持特定料理類型。
    :param user_message: 描述需求的文字，例如「一道菜」或「一道湯」
    :param ingredients: 食材列表
    :param dish_count: 菜的數量
    :param soup_count: 湯的數量
    :param cuisine_type: 料理風格（如中式、日式等）
    :return: 菜或湯的名稱、食材、步驟、來源連結
    """
    prompt = (
        f"用戶希望製作 {cuisine_type} 料理，其中包括 {dish_count} 道菜和 {soup_count} 道湯。"
        f"可用的食材有：{ingredients}。\n"
        "請根據以下格式生成相應數量的食譜：\n\n"
        "對於每道菜，請提供：\n"
        "料理名稱: [料理名稱]\n"
        "食材: [食材列表，單行呈現]\n"
        "食譜內容: [分步驟列點，詳述步驟]\n"
        "來源: [iCook 來源鏈接]\n\n"
        "對於每道湯，請提供：\n"
        "料理名稱: [湯品名稱]\n"
        "食材: [湯品食材列表，單行呈現]\n"
        "食譜內容: [分步驟列點，詳述步驟]\n"
        "來源: [iCook 來源鏈接]\n"
        "請確保每個食譜適合 {cuisine_type} 風味，並以專業大廚的視角提供建議。"
    )

    response = openai.ChatCompletion.create(
        model="gpt-4",
        messages=[
            {"role": "system", "content": f"你是一位專業的 {cuisine_type} 廚師，會根據用戶的需求生成高品質的食譜。"},
            {"role": "user", "content": prompt}
        ],
        max_tokens=1500
    )
    
    recipe_text = response.choices[0].message['content'].strip()
    print(f"ChatGPT 返回的內容: {recipe_text}")

    # 使用 `parse_recipes` 解析
    return parse_recipes(recipe_text)

def parse_recipes(recipe_text):
    """
    解析 ChatGPT 返回的多道菜或湯的食譜。
    
    :param recipe_text: 完整的食譜文本
    :return: 每道食譜包含 (料理名稱, 食材, 步驟, 來源 URL) 的列表
    """
    recipe_pattern = r"料理名稱[:：]\s*(.+?)\n食材[:：]\s*(.+?)\n食譜內容[:：]\s*((?:.|\n)+?)\n來源[:：]\s*(https?://[^\s]+)"
    matches = re.findall(recipe_pattern, recipe_text)

    if matches:
        return matches
    else:
        print("無法解析食譜內容，請檢查輸入格式是否正確")
        return []


import re
def clean_text(text):
    # 去除無效字符和表情符號
    return re.sub(r'[^\w\s,.!?]', '', text)
def create_flex_message(recipe_text, user_id, dish_name, ingredient_text, ingredients, recipe_number, cuisine_type):
    recipe_id = save_recipe_to_db(user_id, dish_name, recipe_text, ingredient_text)
    
    if isinstance(ingredients, list):
        ingredients_str = ','.join(ingredients)
    else:
        ingredients_str = str(ingredients)

    # YouTube 和 iCook 搜尋連結
    youtube_url = f"https://www.youtube.com/results?search_query={dish_name.replace(' ', '+')}"
    icook_url = f"https://icook.tw/search/{dish_name.replace(' ', '%20')}"

    # 設置 Flex Message 結構
    bubble = {
        "type": "bubble",
        "body": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {
                    "type": "text",
                    "text": f"料理 {recipe_number}：{dish_name}（{cuisine_type}）",
                    "wrap": True,
                    "weight": "bold",
                    "size": "xl"
                },
                {
                    "type": "text",
                    "text": f"食材：{ingredient_text}",
                    "wrap": True,
                    "margin": "md",
                    "size": "sm"
                },
                {
                    "type": "text",
                    "text": "食譜內容：",
                    "wrap": True,
                    "weight": "bold",
                    "size": "lg",
                    "margin": "md"
                },
                {
                    "type": "text",
                    "text": recipe_text if recipe_text else "食譜內容缺失",
                    "wrap": True,
                    "margin": "md",
                    "size": "sm"
                }
            ]
        },
        "footer": {
            "type": "box",
            "layout": "vertical",
            "spacing": "sm",
            "contents": [
                {
                    "type": "button",
                    "action": {
                        "type": "postback",
                        "label": "有沒有其他的食譜",
                        "data": f"action=new_recipe&user_id={user_id}&ingredients={ingredients_str}"
                    },
                    "color": "#474242",
                    "style": "primary",
                    "height": "sm"
                },
                {
                    "type": "button",
                    "action": {
                        "type": "postback",
                        "label": "把這個食譜加入我的最愛",
                        "data": f"action=save_favorite&recipe_id={recipe_id}"
                    },
                    "color": "#474242",
                    "style": "primary",
                    "height": "sm"
                },
                {
                    "type": "button",
                    "action": {
                        "type": "uri",
                        "label": "查看 iCook 食譜",
                        "uri": icook_url
                    },
                    "color": "#FFA500",
                    "style": "link",
                    "height": "sm"
                },
                {
                    "type": "button",
                    "action": {
                        "type": "uri",
                        "label": "觀看 YouTube 教學",
                        "uri": youtube_url
                    },
                    "color": "#FFA500",
                    "style": "link",
                    "height": "sm"
                }
            ]
        },
        "styles": {
            "footer": {
                "separator": True
            }
        }
    }
    return bubble

# 處理圖片訊息，進行 Google Cloud Vision 的物體偵測（Label Detection）
@handler.add(MessageEvent, message=ImageMessage)
def handle_image_message(event):
    message_id = event.message.id
    message_content = line_bot_api.get_message_content(message_id)

    image_data = io.BytesIO(message_content.content)
    image = vision.Image(content=image_data.read())

    try:
        response = vision_client.label_detection(image=image)
        labels = response.label_annotations

        if labels:
            detected_labels = [label.description for label in labels]
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
    except Exception as e:
        print(f"Google Vision API 錯誤: {str(e)}")
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=f"圖片辨識過程中發生錯誤: {str(e)}")
        )

# ChatGPT 翻譯並過濾非食材詞彙
def translate_and_filter_ingredients(detected_labels):
    prompt = f"以下是從圖片中辨識出的物體列表：\n{', '.join(detected_labels)}\n請將其翻譯成繁體中文，並只保留與食材相關的詞彙，去除非食材的詞彙。"
    response = openai.ChatCompletion.create(
        model="gpt-4",
        messages=[
            {"role": "system", "content": "你是一個專業的翻譯助手，並且能過濾出與食材相關的內容。"},
            {"role": "user", "content": prompt}
        ]
    )
    processed_text = response.choices[0].message['content'].strip()
    return processed_text

# 問使用者料理需求
def ask_user_for_recipe_info():
    return "您今天想做甚麼樣的料理？幾道菜？"

# 處理使用者需求
@handler.add(PostbackEvent)
def handle_postback(event):
    data = event.postback.data
    params = dict(x.split('=') for x in data.split('&'))
    action = params.get('action')
    user_id = params.get('user_id') or event.source.user_id  # 確保 user_id 不為 None

    if action == 'new_recipe':
        # 回覆"沒問題，請稍後~"
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="沒問題，請稍後~")
        )

        ingredients = params.get('ingredients')
        dish_count = int(params.get('dish_count', 0))  # 從參數中獲取菜的數量
        soup_count = int(params.get('soup_count', 0))  # 從參數中獲取湯的數量
        cuisine_type = params.get('cuisine_type', '一般')  # 默認類型為一般料理

        # 生成符合幾菜幾湯的食譜
        dishes, soups = generate_recipe_response(
            user_message="新的食譜", 
            ingredients=ingredients, 
            dish_count=dish_count, 
            soup_count=soup_count, 
            cuisine_type=cuisine_type
        )

        # 構建 Flex Message 和連結消息
        flex_messages = []
        for i, (dish_name, ingredient_text, recipe_text, icook_url) in enumerate(dishes, start=1):
            flex_messages.append(
                FlexSendMessage(
                    alt_text=f"菜 {i}: {dish_name}",
                    contents=create_flex_message(recipe_text, user_id, dish_name, ingredient_text, ingredients, i)
                )
            )
            # 發送搜尋連結
            youtube_url = f"https://www.youtube.com/results?search_query={dish_name.replace(' ', '+')}"
            line_bot_api.push_message(user_id, [
                TextSendMessage(text=f"iCook 搜尋結果: {icook_url}"),
                TextSendMessage(text=f"YouTube 搜尋結果: {youtube_url}")
            ])

        for i, (soup_name, ingredient_text, recipe_text, icook_url) in enumerate(soups, start=1):
            flex_messages.append(
                FlexSendMessage(
                    alt_text=f"湯 {i}: {soup_name}",
                    contents=create_flex_message(recipe_text, user_id, soup_name, ingredient_text, ingredients, i)
                )
            )
            # 發送搜尋連結
            youtube_url = f"https://www.youtube.com/results?search_query={soup_name.replace(' ', '+')}"
            line_bot_api.push_message(user_id, [
                TextSendMessage(text=f"iCook 搜尋結果: {icook_url}"),
                TextSendMessage(text=f"YouTube 搜尋結果: {youtube_url}")
            ])

        # 發送所有 Flex Messages
        for flex_msg in flex_messages:
            line_bot_api.push_message(user_id, flex_msg)

    elif action == 'save_favorite':
        recipe_id = params.get('recipe_id')
        recipe = get_recipe_from_db(recipe_id)
        if recipe:
            try:
                db.collection('favorites').add({
                    'user_id': user_id,
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
                print(f"儲存最愛時發生錯誤: {e}")
                line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(text="抱歉，儲存過程中發生錯誤。")
                )
        else:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="找不到該食譜，無法加入我的最愛")
            )



def generate_multiple_recipes(dish_count, soup_count, ingredients, cuisine_type=""):
    """
    根據用戶需求生成指定數量的菜和湯，並支持特定料理類型。
    
    :param dish_count: 菜的數量
    :param soup_count: 湯的數量
    :param ingredients: 可用的食材
    :param cuisine_type: 料理風格（如中式、日式等）
    :return: 包含菜和湯的列表
    """
    recipes = {"dishes": [], "soups": []}
    existing_names = set()  # 用於追踪生成的菜名或湯名，避免重複

    # 生成菜
    for _ in range(dish_count):
        while True:
            dish_name, ingredient_text, recipe_text, _ = generate_recipe_response(
                "一道菜", ingredients, 1, 0, cuisine_type
            )

            if dish_name not in existing_names:
                recipes["dishes"].append((dish_name, ingredient_text, recipe_text))
                existing_names.add(dish_name)
                break
            else:
                print("生成的菜名重複，重新生成...")

    # 生成湯
    for _ in range(soup_count):
        while True:
            soup_name, ingredient_text, recipe_text, _ = generate_recipe_response(
                "一道湯", ingredients, 0, 1, cuisine_type
            )

            if soup_name not in existing_names:
                recipes["soups"].append((soup_name, ingredient_text, recipe_text))
                existing_names.add(soup_name)
                break
            else:
                print("生成的湯名重複，重新生成...")

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

    # 檢查訊息中是否包含「道」以及「菜」或「湯」
    dish_count = 0
    soup_count = 0

    # 解析幾菜幾湯
    dish_match = re.search(r"(\d+|[一二兩三四五六七八九十]+)菜", user_message)
    soup_match = re.search(r"(\d+|[一二兩三四五六七八九十]+)湯", user_message)

    if dish_match:
        dish_count = int(dish_match.group(1)) if dish_match.group(1).isdigit() else chinese_to_digit(dish_match.group(1))
    if soup_match:
        soup_count = int(soup_match.group(1)) if soup_match.group(1).isdigit() else chinese_to_digit(soup_match.group(1))

    total_count = dish_count + soup_count

    ingredients = user_ingredients.get(user_id, None)

    if ingredients:
        # 根據需要的數量生成多道料理
        recipes = generate_multiple_recipes(dish_count, soup_count, ingredients)

        # 準備多頁式回覆
        flex_bubbles = [
            create_flex_message(recipe_text, user_id, dish_name, ingredient_text, ingredients, i + 1,"")
            for i, (dish_name, ingredient_text, recipe_text, _) in enumerate(recipes["dishes"] + recipes["soups"])
        ]
        carousel = {
            "type": "carousel",
            "contents": flex_bubbles
        }

        # 回覆 Flex Message
        line_bot_api.reply_message(
            event.reply_token,
            FlexSendMessage(alt_text="您的多道食譜", contents=carousel)
        )

        # 依據類型分別發送文字訊息提示
        line_bot_api.push_message(user_id, TextSendMessage(text=f"您選擇了 {dish_count} 菜和 {soup_count} 湯。"))

    else:
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="請先上傳圖片來辨識食材。")
        )



# 顯示特定食譜的詳細內容 (供 "查看更多" 使用)
@app.route('/api/favorites/<recipe_id>', methods=['GET'])
def get_recipe_detail(recipe_id):
    try:
        recipe_doc = db.collection('recipes').document(recipe_id).get()
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
