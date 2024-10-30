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
import re

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

@handler.add(MessageEvent, message=ImageMessage)
def handle_image_message(event):
    message_id = event.message.id
    print(f"收到圖片訊息，message_id: {message_id}")  # 調試信息
    message_content = line_bot_api.get_message_content(message_id)

    try:
        # 讀取圖片內容
        image_data = io.BytesIO(message_content.content)
        image = vision.Image(content=image_data.read())
        print("圖片已成功讀取")  # 調試信息

        # 使用 Google Cloud Vision API 進行標籤偵測
        response = vision_client.label_detection(image=image)
        labels = response.label_annotations
        print(f"Google Cloud Vision 標籤偵測結果: {[label.description for label in labels]}")  # 調試信息

        if labels:
            detected_labels = [label.description for label in labels]
            processed_text = translate_and_filter_ingredients(detected_labels)
            user_id = event.source.user_id

            if processed_text:
                user_ingredients[user_id] = processed_text
                question_response = ask_user_for_recipe_info()
                line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(text=question_response)
                )
                print(f"處理後的食材列表: {processed_text}")  # 調試信息
            else:
                line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(text="未能識別出任何食材，請嘗試上傳另一張清晰的圖片。")
                )
                print("未能識別出任何食材")  # 調試信息
        else:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="無法辨識出任何物體，請確保圖片中的食材明顯可見。")
            )
            print("無法辨識出任何物體")  # 調試信息
    except Exception as e:
        print(f"Google Vision API 錯誤: {str(e)}")
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=f"圖片辨識過程中發生錯誤: {str(e)}")
        )

# 儲存最愛食譜到 Firebase Firestore
def save_recipe_to_db(user_id, dish_name, recipe_text):
    try:
        doc_ref = db.collection('recipes').document()
        doc_ref.set({
            'user_id': user_id,
            'dish': dish_name,
            'recipe': recipe_text
        })
        return doc_ref.id
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
        recipes_ref = db.collection('recipes').where('user_id', '==', user_id)
        docs = recipes_ref.stream()
        favorites = [{'id': doc.id, **doc.to_dict()} for doc in docs]
        return jsonify(favorites), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

def generate_recipe_response(user_message, ingredients):
    prompt = f"用戶希望做 {user_message}，可用的食材有：{ingredients}。請按照以下格式生成一個適合的食譜：\n\n食譜名稱: [食譜名稱]\n食材: [食材列表]\n步驟: [具體步驟]，字數限制在300字以內。"
    response = openai.ChatCompletion.create(
        model="gpt-4",
        messages=[
            {"role": "system", "content": "你是一位專業的廚師助理，會根據用戶的需求生成食譜。"},
            {"role": "user", "content": prompt}
        ],
        max_tokens=500
    )
    recipe = response.choices[0].message['content'].strip()
    dish_name = None
    recipe_text = None

    try:
        recipe_parts = recipe.split("\n\n")
        for part in recipe_parts:
            if "食譜名稱:" in part:
                dish_name = part.replace("食譜名稱:", "").strip()
            elif "步驟:" in part:
                recipe_text = part.replace("步驟:", "").strip()
    except Exception as e:
        print(f"解析 ChatGPT 回應失敗: {e}")

    if not dish_name:
        dish_name = "未命名料理"
    if not recipe_text:
        recipe_text = "未提供食譜內容"

    print(f"Generated dish_name: {dish_name}, recipe_text: {recipe_text}")  # 調試信息
    return dish_name, recipe_text

def create_flex_message(recipe_text, user_id, dish_name, ingredients):
    recipe_id = save_recipe_to_db(user_id, dish_name, recipe_text)
    if isinstance(ingredients, list):
        ingredients_str = ','.join(ingredients)
    else:
        ingredients_str = str(ingredients)

    bubble = {
        "type": "bubble",
        "body": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {
                    "type": "text",
                    "text": f"料理名稱：{dish_name}",
                    "wrap": True,
                    "weight": "bold",
                    "size": "xl"
                },
                {
                    "type": "text",
                    "text": "您的食譜：",
                    "wrap": True,
                    "weight": "bold",
                    "size": "lg",
                    "margin": "md"
                },
                {
                    "type": "text",
                    "text": recipe_text[:1000] if recipe_text else "食譜內容缺失",
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
                }
            ]
        },
        "styles": {
            "footer": {
                "separator": True
            }
        }
    }

    carousel = {
        "type": "carousel",
        "contents": [bubble]
    }
    return FlexSendMessage(alt_text="您的食譜", contents=carousel)

# 定義中文數字的映射
chinese_to_arabic = {
    '一': 1, '二': 2, '三': 3, '四': 4, '五': 5,
    '六': 6, '七': 7, '八': 8, '九': 9, '十': 10
}

def extract_dish_count(user_message):
    match = re.search(r"([一二三四五六七八九十0-9]+)道", user_message)
    if match:
        count_text = match.group(1)
        if count_text.isdigit():
            return int(count_text)
        elif count_text in chinese_to_arabic:
            return chinese_to_arabic[count_text]
        elif "十" in count_text:
            parts = count_text.split("十")
            if parts[0] == '':
                return 10 + (chinese_to_arabic[parts[1]] if parts[1] in chinese_to_arabic else 0)
            elif parts[1] == '':
                return chinese_to_arabic[parts[0]] * 10
            else:
                return chinese_to_arabic[parts[0]] * 10 + (chinese_to_arabic[parts[1]] if parts[1] in chinese_to_arabic else 0)
    return 1

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_id = event.source.user_id
    user_message = event.message.text
    ingredients = user_ingredients.get(user_id, None)

    if not ingredients:
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="請先上傳圖片來辨識食材。")
        )
        return

    dish_count = extract_dish_count(user_message)
    print(f"user_message: {user_message}, dish_count: {dish_count}, ingredients: {ingredients}")  # 調試信息

    flex_messages = []
    for i in range(dish_count):
        dish_name, recipe_response = generate_recipe_response(user_message, ingredients)
        if dish_name and recipe_response:
            flex_message = create_flex_message(recipe_response, user_id, dish_name, ingredients)
            flex_messages.append(flex_message)
        else:
            print("食譜生成失敗，請檢查 generate_recipe_response 函數的回傳結果")

    if flex_messages:
        line_bot_api.reply_message(event.reply_token, flex_messages[:5])
    else:
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="無法生成食譜，請稍後再試。")
        )

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

@app.route("/health", methods=["GET"])
def health_check():
    return "OK", 200

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
