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

# 儲存最愛食譜到 Firebase Firestore
def save_recipe_to_db(user_id, dish_name, recipe_text, ingredient_text):
    try:
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

def generate_recipe_response(dish_count, soup_count, ingredients):
    prompt = (
        f"用戶希望製作 {dish_count} 道菜和 {soup_count} 道湯。"
        f"可用的食材有：{ingredients}。\n"
        "請為每道菜或湯生成食譜，格式如下：\n\n"
        "料理名稱: [料理名稱]\n"
        "食材: [食材列表，單行呈現]\n"
        "食譜內容: [分步驟列點，詳述步驟]\n"
    )

    response = openai.ChatCompletion.create(
        model="gpt-4",
        messages=[
            {"role": "system", "content": "你是一位專業的廚師，會根據用戶需求生成高品質的食譜。"},
            {"role": "user", "content": prompt}
        ],
        max_tokens=1500
    )
    recipe_text = response.choices[0].message['content'].strip()
    print(f"ChatGPT 返回的內容:\n{recipe_text}")

    # 修改正則以確保匹配到正確的食譜結構
    recipe_pattern = r"料理名稱[:：]\s*(.+?)\n食材[:：]\s*(.+?)\n食譜內容[:：]\s*((?:\d+\.\s?.+?\n?)+)"
    matches = re.findall(recipe_pattern, recipe_text)

    if not matches:
        print("未成功解析到食譜內容，請檢查格式")
    return matches

def create_flex_message(dish_name, ingredient_text, recipe_text, user_id, recipe_number):
    recipe_id = save_recipe_to_db(user_id, dish_name, recipe_text, ingredient_text)

    bubble = {
        "type": "bubble",
        "body": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {"type": "text", "text": f"料理 {recipe_number}：{dish_name}", "weight": "bold", "size": "xl", "wrap": True},
                {"type": "text", "text": f"食材：{ingredient_text}", "margin": "md", "size": "sm", "wrap": True},
                {"type": "text", "text": "食譜內容：", "weight": "bold", "size": "lg", "margin": "md"},
                {"type": "text", "text": recipe_text, "margin": "md", "size": "sm", "wrap": True}
            ]
        },
        "footer": {
            "type": "box",
            "layout": "vertical",
            "spacing": "sm",
            "contents": [
                {
                    "type": "button",
                    "action": {"type": "postback", "label": "有沒有其他的食譜", "data": f"action=new_recipe&user_id={user_id}"},
                    "style": "primary"
                },
                {
                    "type": "button",
                    "action": {"type": "postback", "label": "把這個食譜加入我的最愛", "data": f"action=save_favorite&recipe_id={recipe_id}"},
                    "style": "primary"
                }
            ]
        }
    }
    return bubble

# Image recognition and message handling
@handler.add(MessageEvent, message=ImageMessage)
def handle_image_message(event):
    message_id = event.message.id
    message_content = line_bot_api.get_message_content(message_id)
    image_data = io.BytesIO(message_content.content)
    image = vision.Image(content=image_data.read())

    try:
        response = vision_client.label_detection(image=image)
        labels = response.label_annotations
        detected_labels = [label.description for label in labels]
        processed_text = translate_and_filter_ingredients(detected_labels)
        user_id = event.source.user_id

        if processed_text:
            user_ingredients[user_id] = processed_text
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="請輸入需要幾道菜與幾道湯。"))
        else:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="未能識別出任何食材，請上傳清晰的圖片。"))
    except Exception as e:
        print(f"Google Vision API 錯誤: {e}")
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"圖片辨識發生錯誤: {e}"))

def translate_and_filter_ingredients(detected_labels):
    prompt = f"以下是從圖片中辨識出的物體列表：{', '.join(detected_labels)}。請將其翻譯成繁體中文，並只保留食材相關詞彙。"
    response = openai.ChatCompletion.create(
        model="gpt-4",
        messages=[
            {"role": "system", "content": "你是一個專業的翻譯助手，負責過濾非食材詞彙。"},
            {"role": "user", "content": prompt}
        ]
    )
    return response.choices[0].message['content'].strip()

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_id = event.source.user_id
    user_message = event.message.text
    dish_match = re.search(r"(\d+|[一二兩三四五六七八九十]+)菜", user_message)
    soup_match = re.search(r"(\d+|[一二兩三四五六七八九十]+)湯", user_message)
    
    dish_count = int(dish_match.group(1)) if dish_match else 0
    soup_count = int(soup_match.group(1)) if soup_match else 0
    ingredients = user_ingredients.get(user_id)

    if ingredients:
        recipes = generate_recipe_response(dish_count, soup_count, ingredients)
        if recipes:
            flex_messages = [
                create_flex_message(dish_name, ingredient_text, recipe_text, user_id, i + 1)
                for i, (dish_name, ingredient_text, recipe_text) in enumerate(recipes)
            ]
            line_bot_api.reply_message(
                event.reply_token,
                FlexSendMessage(alt_text="您的食譜", contents={"type": "carousel", "contents": flex_messages})
            )
        else:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="未能生成食譜，請稍後再試。"))
    else:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="請先上傳圖片辨識食材。"))

# Webhook
@app.route("/callback", methods=["POST"])
def callback():
    body = request.get_data(as_text=True)
    signature = request.headers["X-Line-Signature"]
    handler.handle(body, signature)
    return "OK"

if __name__ == "__main__":
    app.run(port=int(os.getenv("PORT", 5000)))
