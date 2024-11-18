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
        return doc_ref.id
    except Exception as e:
        print(f"Firestore 插入錯誤: {e}")
        return None

def generate_recipe_response(dish_count, soup_count, ingredients):
    """
    生成包含幾道菜和幾道湯的食譜
    """
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
    print(f"ChatGPT 返回的內容: {recipe_text}")

    recipe_pattern = r"料理名稱[:：]\s*(.+?)\n食材[:：]\s*(.+?)\n食譜內容[:：]\s*((?:.|\n)+?)"
    matches = re.findall(recipe_pattern, recipe_text)

    return matches

def create_flex_message(dish_name, ingredient_text, recipe_text, user_id, recipe_number):
    recipe_id = save_recipe_to_db(user_id, dish_name, recipe_text, ingredient_text)

    bubble = {
        "type": "bubble",
        "body": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {
                    "type": "text",
                    "text": f"料理 {recipe_number}：{dish_name}",
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
                    "text": recipe_text,
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
                        "data": f"action=new_recipe&user_id={user_id}"
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
    return bubble

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
        flex_messages = [
            create_flex_message(dish_name, ingredient_text, recipe_text, user_id, i + 1)
            for i, (dish_name, ingredient_text, recipe_text) in enumerate(recipes)
        ]
        line_bot_api.reply_message(
            event.reply_token,
            FlexSendMessage(alt_text="您的多道食譜", contents={"type": "carousel", "contents": flex_messages})
        )
    else:
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="請先上傳圖片辨識食材。")
        )

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
