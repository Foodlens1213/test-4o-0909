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
    # get X-Line-Signature header value
    signature = request.headers["X-Line-Signature"]

    # get request body as text
    body = request.get_data(as_text=True)
    app.logger.info(f"Request body: {body}")

    # handle webhook body
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)

    return "OK"

# Handle text messages sent to the bot
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_message = event.message.text

    # 檢查是否為觸發多頁訊息的關鍵字
    if user_message in ['料理推薦', '食譜推薦']:  # 關鍵字列表
        print(f"Received keyword: {user_message}, triggering multi-page message.")  # 日誌顯示觸發了多頁訊息
        return  # 多頁訊息由LINE自動回覆處理

    # 非關鍵字訊息，由ChatGPT處理
    print(f"Received non-keyword message: {user_message}, sending to ChatGPT.")
    try:
        response = openai.ChatCompletion.create(
            model="gpt-4",  # 使用 GPT-4 模型，或 "gpt-3.5-turbo"
            messages=[
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": user_message},
            ]
        )
        reply_text = response.choices[0].message['content'].strip()
        print(f"ChatGPT response: {reply_text}")  # 日誌記錄 ChatGPT 回應
    except Exception as e:
        print(f"Error calling ChatGPT: {e}")  # 日誌記錄錯誤
        reply_text = "抱歉，我暫時無法處理您的請求。"

    # 將回應發送給使用者
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=reply_text)
    )

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))  # Render will provide the PORT env variable
    app.run(host="0.0.0.0", port=port)
