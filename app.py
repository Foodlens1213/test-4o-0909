# -*- coding: utf-8 -*-
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage, ImageMessage
import openai
import os
from google.cloud import vision
from dotenv import load_dotenv
import io

# Load environment variables from .env file
load_dotenv()

app = Flask(__name__)

# LINE Bot API and Webhook settings
line_bot_api = LineBotApi(os.getenv("LINE_CHANNEL_ACCESS_TOKEN"))
handler = WebhookHandler(os.getenv("LINE_CHANNEL_SECRET"))

# OpenAI API key
openai.api_key = os.getenv("OPENAI_API_KEY")

# 讀取 Google Cloud 憑證內容並寫入到臨時文件
google_credentials_content = os.getenv("GOOGLE_APPLICATION_CREDENTIALS_CONTENT")
if google_credentials_content:
    with open("/tmp/google-credentials.json", "w") as f:
        f.write(google_credentials_content)
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = "/tmp/google-credentials.json"

# Initialize Google Cloud Vision API client
vision_client = vision.ImageAnnotatorClient()

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
    user_message = event.message.text
    print(f"Received message: {user_message}")

    if user_message in ['料理推薦', '食譜推薦']:
        print(f"Received keyword: {user_message}, triggering multi-page message.")
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
    except Exception as e:
        print(f"Error calling ChatGPT: {e}")
        reply_text = "抱歉，我暫時無法處理您的請求。"

    # 回覆給 LINE 使用者
    try:
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=reply_text)
        )
        print(f"Replied with message: {reply_text}")
    except Exception as e:
        print(f"Error sending reply: {e}")

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

    # 回覆給 LINE 使用者
    try:
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=reply_text)
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

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
