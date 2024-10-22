from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, ImageMessage, TextSendMessage
import os
import requests
import base64
import openai

app = Flask(__name__)

# LINE Bot API設定
line_bot_api = LineBotApi(os.getenv("LINE_CHANNEL_ACCESS_TOKEN"))
handler = WebhookHandler(os.getenv("LINE_CHANNEL_SECRET"))

# 設定你的 OpenAI API 金鑰（ChatGPT）
openai.api_key = os.getenv("OPENAI_API_KEY")

# 使用環境變數中的 Google Gemini API 金鑰
GEMINI_PRO_VISION_API_KEY = os.getenv("GEMINI_PRO_VISION_API_KEY")

# 添加根路徑處理
@app.route("/", methods=["GET"])
def index():
    return "Welcome to the Flask App!", 200
    
# Health check route for Render
@app.route("/health", methods=["GET"])
def health_check():
    return "OK", 200

# 處理LINE Webhook的callback路徑
@app.route("/callback", methods=["POST"])
def callback():
    # 獲取 LINE 請求標頭中的簽名
    signature = request.headers['X-Line-Signature']
    # 獲取請求中的消息
    body = request.get_data(as_text=True)

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)

    return 'OK', 200  # 確保返回 200 狀態碼給 LINE 平台

# 處理圖片訊息
@handler.add(MessageEvent, message=ImageMessage)
def handle_image_message(event):
    try:
        print("收到圖片訊息，開始處理")

        # 從 LINE 伺服器獲取圖片
        message_content = line_bot_api.get_message_content(event.message.id)
        print("圖片已從 LINE 伺服器獲取")

        # 保存圖片
        with open("image1.jpg", "wb") as f:
            for chunk in message_content.iter_content():
                f.write(chunk)
        print("圖片已保存")

        # 圖片進行 base64 編碼
        with open("image1.jpg", "rb") as image_file:
            encoded_image = base64.b64encode(image_file.read()).decode('utf-8')
        print("圖片已進行 base64 編碼")

        # 發送請求至 Google Gemini API
        endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-pro-vision:generateContent?key={GEMINI_PRO_VISION_API_KEY}"
        headers = {'Content-Type': 'application/json'}
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
        print("發送請求到 Gemini API")

        # 發送請求並檢查回應
        response = requests.post(endpoint, headers=headers, json=request_payload)

        if response.status_code == 200:
            response_json = response.json()
            print("成功接收到 Gemini API 的回應")
            response_text = response_json.get('text', '無法辨識圖片內容')
            print(f"Gemini API 辨識結果：{response_text}")

            # 使用 ChatGPT 進一步處理圖片結果
            chat_response = openai.ChatCompletion.create(
                model="gpt-4",
                messages=[
                    {"role": "system", "content": "You are a helpful assistant."},
                    {"role": "user", "content": f"圖片辨識結果: {response_text}"}
                ]
            )

            chat_reply = chat_response['choices'][0]['message']['content'].strip()
            print(f"ChatGPT 回應：{chat_reply}")

            # 回傳辨識結果給使用者
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=chat_reply)
            )

        else:
            print(f"API 請求失敗，狀態碼: {response.status_code}, 回應內容: {response.text}")
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=f"API 請求失敗，狀態碼: {response.status_code}, 錯誤訊息: {response.text}")
            )
    except Exception as e:
        print(f"處理圖片時出現錯誤：{str(e)}")
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="圖片處理失敗，請稍後再試")
        )

if __name__ == "__main__":
    app.run(debug=True)
