# -*- coding: utf-8 -*-
from flask import Flask, request, abort, render_template
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage, ImageMessage, FlexSendMessage, PostbackAction, URIAction
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
vision_client = vision.ImageAnnotatorClient()

# 設定 MySQL 連接
def get_db_connection():
    return mysql.connector.connect(
        host=os.getenv('MYSQL_HOST'),
        user=os.getenv('MYSQL_USER'),
        password=os.getenv('MYSQL_PASSWORD'),
        database=os.getenv('MYSQL_DATABASE')
    )

# 儲存最愛到資料庫
def save_to_favorites(user_id, favorite_text):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO favorites (user_id, favorite) VALUES (%s, %s)", (user_id, favorite_text))
    conn.commit()
    cursor.close()
    conn.close()

# 查詢最愛
def get_favorites(user_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT favorite FROM favorites WHERE user_id = %s", (user_id,))
    favorites = cursor.fetchall()
    cursor.close()
    conn.close()
    return favorites

@app.route("/")
def home():
    return "Hello! This is your LINE Bot server."

# 健康檢查路徑
@app.route("/health", methods=["GET"])
def health_check():
    return "OK", 200  # 健康檢查回應

@app.route("/callback", methods=["POST"])
def callback():
    # Get request body as text
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

# 處理來自 LINE 的文字訊息事件
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_id = event.source.user_id
    user_message = event.message.text
    print(f"Received message: {user_message}")

    if user_message == "我的最愛":
        show_favorites(event, user_id)
        return

    if user_message.startswith("加入我的最愛"):
        # 假設格式為 "加入我的最愛:訊息內容"
        try:
            _, favorite_text = user_message.split(":", 1)
            add_to_favorites(event, user_id, favorite_text.strip())
        except ValueError:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="請使用格式 '加入我的最愛:訊息內容'")
            )
        return

    # 使用 ChatGPT 處理非關鍵字訊息
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

        # 使用 Flex Message 回應
        reply_message = create_flex_message(reply_text, user_id)
    except Exception as e:
        print(f"Error calling ChatGPT: {e}")
        reply_message = TextSendMessage(text="抱歉，我暫時無法處理您的請求。")

    # 回覆給 LINE 使用者
    try:
        line_bot_api.reply_message(
            event.reply_token,
            reply_message
        )
        print(f"Replied with message: {reply_message}")
    except Exception as e:
        print(f"Error sending reply: {e}")

# 創建多頁 Flex Message，並加入 LIFF 連結
def create_flex_message(reply_text, user_id):
    """生成 Flex Message，包含回覆和加入我的最愛按鈕"""
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
                        "data": f"加入我的最愛:{reply_text}"
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

# 處理來自 LINE 的圖片訊息事件
@handler.add(MessageEvent, message=ImageMessage)
def handle_image(event):
    print("Received image, processing with Google Cloud Vision API...")

    # 下載圖片
    message_content = line_bot_api.get_message_content(event.message.id)
    image_path = "image.jpg"
    with open(image_path, "wb") as f:
        for chunk in message_content.iter_content():
            f.write(chunk)

    # 使用 Google Cloud Vision API 分析圖片
    labels = analyze_image_with_google_vision(image_path)
    if labels:
        reply_text = f"識別到的食材：{', '.join(labels)}"
    else:
        reply_text = "無法識別圖片中的食材，請嘗試另一張圖片。"

    # 使用 Flex Message 回應
    reply_message = create_flex_message(reply_text, event.source.user_id)

    # 回覆給 LINE 使用者
    try:
        line_bot_api.reply_message(
            event.reply_token,
            reply_message
        )
        print(f"Replied with message: {reply_text}")
    except Exception as e:
        print(f"Error sending reply: {e}")

def analyze_image_with_google_vision(image_path):
    """使用 Google Cloud Vision API 分析圖片並返回標籤"""
    try:
        with io.open(image_path, "rb") as image_file:
            content = image_file.read()
        image = vision.Image(content=content)

        # 呼叫 Google Cloud Vision API 進行標籤檢測
        response = vision_client.label_detection(image=image)
        labels = response.label_annotations

        # 提取標籤名稱（食材名稱）
        result_labels = [label.description for label in labels]
        print(f"識別結果：{result_labels}")
        return result_labels
    except Exception as e:
        print(f"Error analyzing image: {e}")
        return None

# 新增到我的最愛
def add_to_favorites(event, user_id, message):
    save_to_favorites(user_id, message)
    reply_text = f"已將訊息新增至最愛：{message}"
    line_bot_api.reply_message(
        event.reply_token, 
        TextSendMessage(text=reply_text)
    )
    print(f"Added to favorites: {message}")

# 顯示我的最愛
def show_favorites(event, user_id):
    favorites = get_favorites(user_id)
    if favorites:
        fav_list = "\n".join([fav[0] for fav in favorites])
        reply_text = f"您的最愛：\n{fav_list}"
    else:
        reply_text = "您的最愛清單是空的。"
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=reply_text)
    )
    print(f"Displayed favorites for user {user_id}")

# 顯示我的最愛的網頁
@app.route("/favorites/<user_id>")
def show_favorites_web(user_id):
    favorites = get_favorites(user_id)
    if favorites:
        fav_list = [fav[0] for fav in favorites]
    else:
        fav_list = []
    return render_template("favorites.html", fav_list=fav_list, user_id=user_id)

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
