# -*- coding: utf-8 -*-
from flask import Flask, request, abort, jsonify, render_template
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage, ImageMessage, FlexSendMessage, PostbackAction, PostbackEvent
import openai
import os
from google.cloud import vision
from dotenv import load_dotenv
import io
import mysql.connector

# Load environment variables from .env file
load_dotenv()

app = Flask(__name__)

# LINE Bot API and Webhook settings
line_bot_api = LineBotApi(os.getenv("LINE_CHANNEL_ACCESS_TOKEN"))
handler = WebhookHandler(os.getenv("LINE_CHANNEL_SECRET"))

# OpenAI API key
openai.api_key = os.getenv("OPENAI_API_KEY")

# Initialize Google Cloud Vision API client
google_credentials_content = os.getenv("GOOGLE_APPLICATION_CREDENTIALS_CONTENT")
if google_credentials_content:
    credentials_path = "/tmp/google-credentials.json"
    with open(credentials_path, "w") as f:
        f.write(google_credentials_content)
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = credentials_path

vision_client = vision.ImageAnnotatorClient()

# MySQL connection setup
def get_db_connection():
    try:
        conn = mysql.connector.connect(
            host=os.getenv('MYSQL_HOST'),
            user=os.getenv('MYSQL_USER'),
            database=os.getenv('MYSQL_DATABASE')
        )
        print("資料庫連線成功")
        return conn
    except mysql.connector.Error as err:
        print(f"資料庫連線錯誤: {err}")
        return None

# Save to favorites
def save_to_favorites(user_id, favorite_text):
    try:
        conn = get_db_connection()
        if conn is None:
            print("無法連接資料庫")
            return

        cursor = conn.cursor()
        sql = "INSERT INTO favorites (user_id, favorite) VALUES (%s, %s)"
        values = (user_id, favorite_text)
        
        cursor.execute(sql, values)
        conn.commit()
        print(f"已成功儲存至資料庫: {user_id}, {favorite_text}")

        cursor.close()
        conn.close()
    except mysql.connector.Error as err:
        print(f"資料庫插入錯誤: {err}")

# Fetch favorites
def get_favorites(user_id):
    try:
        conn = get_db_connection()
        if conn is None:
            return []

        cursor = conn.cursor()
        cursor.execute("SELECT favorite FROM favorites WHERE user_id = %s", (user_id,))
        favorites = cursor.fetchall()
        cursor.close()
        conn.close()
        return favorites
    except mysql.connector.Error as err:
        print(f"資料庫查詢錯誤: {err}")
        return []

@app.route("/")
def home():
    return "Hello! This is your LINE Bot server."

# Health check route
@app.route("/health", methods=["GET"])
def health_check():
    return "OK", 200

# Webhook callback for LINE messages
@app.route("/callback", methods=["POST"])
def callback():
    body = request.get_data(as_text=True)
    print(f"Received Webhook request: Body: {body}")

    try:
        signature = request.headers["X-Line-Signature"]
        handler.handle(body, signature)
    except InvalidSignatureError:
        print("Invalid Signature Error!")
        abort(400)
    except Exception as e:
        print(f"Error handling webhook request: {e}")
        abort(500)

    return "OK"

# Handle text messages from LINE
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_id = event.source.user_id
    user_message = event.message.text
    print(f"Received message: {user_message}")

    if user_message == "我的最愛":
        show_favorites(event, user_id)
        return

    if user_message.startswith("加入我的最愛"):
        try:
            _, favorite_text = user_message.split(":", 1)
            add_to_favorites(event, user_id, favorite_text.strip())
        except ValueError:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="請使用格式 '加入我的最愛:訊息內容'")
            )
        return

    # Use ChatGPT to handle non-keyword messages
    print(f"Received non-keyword message: {user_message}, sending to ChatGPT.")
    try:
        response = openai.ChatCompletion.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": user_message},
            ]
        )
        reply_text = response.choices[0].message['content'].strip()
        print(f"ChatGPT response: {reply_text}")

        # Use Flex Message to respond
        reply_message = create_flex_message(reply_text, user_id)
    except Exception as e:
        print(f"Error calling ChatGPT: {e}")
        reply_message = TextSendMessage(text="抱歉，我暫時無法處理您的請求。")

    try:
        line_bot_api.reply_message(event.reply_token, reply_message)
        print(f"Replied with message: {reply_message}")
    except Exception as e:
        print(f"Error sending reply: {e}")

# Create Flex Message with LIFF link
def create_flex_message(reply_text, user_id):
    liff_url = f"https://liff.line.me/{os.getenv('LIFF_ID')}/favorites/{user_id}"
    bubble = {
        "type": "bubble",
        "body": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {
                    "type": "text",
                    "text": reply_text,
                    "wrap": True,
                    "size": "md"
                },
                {
                    "type": "button",
                    "action": {
                        "type": "postback",
                        "label": "加入我的最愛",
                        "data": f"action=add_favorite&user_id={user_id}&message={reply_text}"
                    },
                    "margin": "md",
                    "color": "#1DB446"
                },
                {
                    "type": "button",
                    "action": {
                        "type": "uri",
                        "label": "查看我的最愛",
                        "uri": liff_url
                    },
                    "margin": "md",
                    "color": "#0000FF"
                }
            ]
        }
    }
    carousel = {
        "type": "carousel",
        "contents": [bubble]
    }
    return FlexSendMessage(alt_text="多頁訊息", contents=carousel)

# Handle Postback Events
@handler.add(PostbackEvent)
def handle_postback(event):
    data = event.postback.data
    params = dict(x.split('=') for x in data.split('&'))

    action = params.get('action')
    user_id = params.get('user_id')
    message = params.get('message')

    print(f"Postback received: action={action}, user_id={user_id}, message={message}")

    if action == 'add_favorite':
        add_to_favorites(event, user_id, message)

# Add to favorites
def add_to_favorites(event, user_id, message):
    save_to_favorites(user_id, message)
    reply_text = f"已將訊息新增至最愛：{message}"
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
    print(f"Added to favorites: {message}")

# Show user's favorites
def show_favorites(event, user_id):
    favorites = get_favorites(user_id)
    if favorites:
        fav_list = "\n".join([fav[0] for fav in favorites])
        reply_text = f"您的最愛：\n{fav_list}"
    else:
        reply_text = "您的最愛清單是空的。"
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
    print(f"Displayed favorites for user {user_id}")

# Show favorites via web
@app.route("/favorites/<user_id>")
def show_favorites_web(user_id):
    favorites = get_favorites(user_id)
    if favorites:
        fav_list = [fav[0] for fav in favorites]
    else:
        fav_list = []
    
    # Return JSON for LIFF application
    return jsonify({
        "user_id": user_id,
        "favorites": fav_list
    })

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
