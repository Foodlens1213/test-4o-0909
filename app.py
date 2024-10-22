# -*- coding: utf-8 -*-
from flask import Flask, request, abort, jsonify
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage, PostbackEvent, ImageMessage
import os
import requests
import base64
import openai

app = Flask(__name__)

# LINE Bot API settings
line_bot_api = LineBotApi(os.getenv("LINE_CHANNEL_ACCESS_TOKEN"))
handler = WebhookHandler(os.getenv("LINE_CHANNEL_SECRET"))

# 設定你的 OpenAI API 金鑰（ChatGPT）
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
openai.api_key = OPENAI_API_KEY

# 從環境變數中獲取 Google Gemini API 金鑰
GEMINI_PRO_VISION_API_KEY = os.getenv("GEMINI_PRO_VISION_API_KEY")


# 處理來自 LINE 的圖片訊息
@handler.add(MessageEvent, message=ImageMessage)
def handle_image_message(event):
    # 從 LINE 伺服器獲取圖片
    message_content = line_bot_api.get_message_content(event.message.id)
    
    # 保存圖片
    with open("image1.jpg", "wb") as f:
        for chunk in message_content.iter_content():
            f.write(chunk)
    
    # 圖片進行 base64 編碼
    with open("image1.jpg", "rb") as image_file:
        encoded_image = base64.b64encode(image_file.read()).decode('utf-8')

    # 構建 API 請求
    endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-pro-vision:generateContent?key={GEMINI_PRO_VISION_API_KEY}"

    headers = {
        'Content-Type': 'application/json'
    }

    request_payload = {
        'contents': [
            {
                'parts': [
                    {'text': '看到什麼'},
                    {
                        'inline_data': {
                            'mime_type': 'image/jpeg',
                            'data': encoded_image
                        }
                    }
                ]
            }
        ]
    }

    # 發送請求到 Gemini-Pro-Vision
    response = requests.post(endpoint, headers=headers, json=request_payload)

    # 處理回應
    if response.status_code == 200:
        response_json = response.json()

        # 提取辨識結果
        def get_value(data, key):
            if isinstance(data, dict):
                for k, v in data.items():
                    if k == key:
                        return v
                    else:
                        value = get_value(v, key)
                        if value is not None:
                            return value
            elif isinstance(data, list):
                for v in data:
                    value = get_value(v, key)
                    if value is not None:
                        return value
            return None

        response_text = get_value(response_json, "text")

        # 使用 ChatGPT 進一步處理圖片結果
        chat_response = openai.ChatCompletion.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": f"圖片辨識結果: {response_text}"}
            ]
        )

        chat_reply = chat_response['choices'][0]['message']['content'].strip()

        # 回傳辨識結果給使用者
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=chat_reply)
        )

    else:
        # 印出錯誤詳情
        print(f"API 請求失敗，狀態碼: {response.status_code}, 回應內容: {response.text}")
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=f"API 請求失敗，狀態碼: {response.status_code}, 錯誤訊息: {response.text}")
        )


if __name__ == "__main__":
    app.run(debug=True)
