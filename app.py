import base64
import requests
import openai
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, ImageMessage, TextSendMessage
import os

app = Flask(__name__)

# LINE Bot API 設定
line_bot_api = LineBotApi(os.getenv("LINE_CHANNEL_ACCESS_TOKEN"))
handler = WebhookHandler(os.getenv("LINE_CHANNEL_SECRET"))

# OpenAI API 設定
openai.api_key = os.getenv("OPENAI_API_KEY")

# Generative Language API 設定
GEN_LANG_API_KEY = os.getenv("GEN_LANG_API_KEY")
GEN_LANG_API_URL = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEN_LANG_API_KEY}"

# 添加根路徑處理
@app.route("/", methods=["GET"])
def index():
    return "Welcome to the Flask App!", 200
    
# Health check route
@app.route("/health", methods=["GET"])
def health_check():
    return "OK", 200

# 處理LINE Webhook的callback路徑
@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)

    return 'OK', 200

# 使用 Generative Language API 生成描述
def generate_image_description(image_path, text_input):
    with open(image_path, "rb") as image_file:
        encoded_image = base64.b64encode(image_file.read()).decode("utf-8")

    # 創建請求的資料
    payload = {
        "contents": [
            {
                "parts": [
                    {"text": text_input},
                    {
                        "inline_data": {
                            "mime_type": "image/jpeg",
                            "data": encoded_image
                        }
                    }
                ]
            }
        ]
    }

    # 發送請求
    headers = {"Content-Type": "application/json"}
    response = requests.post(GEN_LANG_API_URL, headers=headers, json=payload)
    # 打印具體的錯誤訊息來調試
    if response.status_code == 400:
        print(f"錯誤訊息: {response.json()}")

# 這樣可以幫助你查看回應中具體的錯誤提示
    # 處理回應
    if response.status_code == 200:
        return response.json().get("text", "無法生成描述")
    else:
        return f"API 請求失敗，狀態碼: {response.status_code}"

# 使用 ChatGPT 進行進一步處理
def process_description_with_chatgpt(description, user_request):
    chat_response = openai.ChatCompletion.create(
        model="gpt-4",
        messages=[
            {"role": "system", "content": "你是一個樂於助人的助手。"},
            {"role": "user", "content": f"圖片描述: {description}. 根據描述，請回答: {user_request}"}
        ]
    )
    return chat_response['choices'][0]['message']['content']

# 處理圖片訊息
@handler.add(MessageEvent, message=ImageMessage)
def handle_image_message(event):
    try:
        # 從 LINE 伺服器獲取圖片
        message_content = line_bot_api.get_message_content(event.message.id)

        # 保存圖片
        image_path = "image1.jpg"
        with open(image_path, "wb") as f:
            for chunk in message_content.iter_content():
                f.write(chunk)

        # 圖片描述的文字輸入
        text_input = "請描述這張圖片的內容。"

        # 使用 Generative Language API 生成描述
        description = generate_image_description(image_path, text_input)
        print(f"圖片描述: {description}")

        # 假設用戶發送了進一步的需求
        user_request = "請詳細說明這個圖片描述。"

        # 使用 ChatGPT 處理描述並生成回應
        final_response = process_description_with_chatgpt(description, user_request)

        # 回應結果給使用者
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=final_response)
        )

    except Exception as e:
        print(f"處理圖片時發生錯誤: {str(e)}")
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="圖片處理失敗，請稍後再試")
        )

if __name__ == "__main__":
    app.run(debug=True)
