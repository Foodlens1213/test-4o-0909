# -*- coding: utf-8 -*-
"""
Created on Mon Sep  9 21:40:00 2024

@author: 蔡
"""

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
    
@app.route("/callback", methods=["POST"])
def callback():
    # Get request body as text
    body = request.get_data(as_text=True)

    # Log the request body for debugging purposes
    print(f"Received Webhook request: Body: {body}")

    try:
        # Handle the Webhook request
        signature = request.headers["X-Line-Signature"]  # Fetch the signature
        handler.handle(body, signature)  # Properly handle the webhook
    except InvalidSignatureError:
        print("Invalid Signature Error!")  # 捕捉簽名錯誤
        abort(400)
    except Exception as e:
        print(f"Error handling webhook request: {e}")  # 捕捉其他錯誤
        abort(500)

    return "OK"

# 處理來自 LINE 的訊息事件
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_message = event.message.text
    print(f"Received message: {user_message}")

    # 檢查是否為觸發多頁訊息的關鍵字
    if user_message in ['料理推薦', '食譜推薦']:  # 關鍵字列表
        print(f"Received keyword: {user_message}, triggering multi-page message.")
        return  # 多頁訊息由 LINE 自動回覆處理

    # 非關鍵字訊息，由 ChatGPT 處理
    print(f"Received non-keyword message: {user_message}, sending to ChatGPT.")
    try:
        # 使用 OpenAI GPT 生成回覆
        response = openai.ChatCompletion.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": user_message},
            ]
        )
        reply_text = response.choices[0].message['content'].strip()
        print(f"ChatGPT response: {reply_text}")  # 打印 ChatGPT 的回應
    except Exception as e:
        print(f"Error calling ChatGPT: {e}")
        reply_text = "抱歉，我暫時無法處理您的請求。"

    # 回覆給 LINE 使用者
    try:
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=reply_text)
        )
        print(f"Replied with message: {reply_text}")  # 記錄回應的內容
    except Exception as e:
        print(f"Error sending reply: {e}")  # 捕捉回覆時的錯誤

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))  # 使用 Render 提供的 PORT 環境變數
    app.run(host="0.0.0.0", port=port)
