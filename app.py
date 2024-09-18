# -*- coding: utf-8 -*-
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
import openai
import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

app = Flask(__name__)

# LINE Bot API and Webhook settings
line_bot_api = LineBotApi(os.getenv("LINE_CHANNEL_ACCESS_TOKEN"))
handler = WebhookHandler(os.getenv("LINE_CHANNEL_SECRET"))

# OpenAI API key
openai.api_key = os.getenv("OPENAI_API_KEY")

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

# 處理來自 LINE 的訊息事件
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

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
