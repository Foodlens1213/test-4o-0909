# -*- coding: utf-8 -*-
"""
Created on Mon Sep  9 21:40:00 2024

@author: 蔡
"""

from flask import Flask, request, abort
from linebot.models import MessageEvent, TextMessage, TextSendMessage
import openai
import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

app = Flask(__name__)

# LINE Bot API and Webhook settings
line_bot_api = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
line_channel_secret = os.getenv("LINE_CHANNEL_SECRET")

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

    # 模擬手動處理 Webhook 請求
    try:
        # 直接解析 Webhook 請求
        event_data = request.get_json()
        events = event_data.get("events", [])
        if not events:
            print("No events found in the request body")
            return "OK"

        for event in events:
            if event["type"] == "message" and event["message"]["type"] == "text":
                print("Processing message event...")  # 日誌：開始處理訊息事件
                handle_message(event)  # 手動處理消息事件

    except Exception as e:
        print(f"Error handling webhook request: {e}")
        abort(500)

    return "OK"

# 手動處理收到的消息
def handle_message(event):
    user_message = event["message"]["text"]
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

    # 打印回覆的訊息
    print(f"Replied with message: {reply_text}")

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
