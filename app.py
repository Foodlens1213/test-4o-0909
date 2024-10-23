from flask import Flask, request, abort, jsonify
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage, ImageMessage
import openai
import os
from google.cloud import vision
from dotenv import load_dotenv
import io
import mysql.connector

# 載入環境變數
load_dotenv()
app = Flask(__name__)

# LINE Bot API 和 Webhook 設定
line_bot_api = LineBotApi(os.getenv("LINE_CHANNEL_ACCESS_TOKEN"))
handler = WebhookHandler(os.getenv("LINE_CHANNEL_SECRET"))

# OpenAI API 金鑰
openai.api_key = os.getenv("OPENAI_API_KEY")

# 初始化 Google Cloud Vision API 客戶端
google_credentials_content = os.getenv("GOOGLE_APPLICATION_CREDENTIALS_CONTENT")
if google_credentials_content:
    credentials_path = "/tmp/google-credentials.json"
    with open(credentials_path, "w") as f:
        f.write(google_credentials_content)
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = credentials_path
vision_client = vision.ImageAnnotatorClient()

# 儲存處理後的食材資料（供後續使用）
user_ingredients = {}

# 處理圖片訊息，進行 Google Cloud Vision 辨識
@handler.add(MessageEvent, message=ImageMessage)
def handle_image_message(event):
    message_id = event.message.id
    message_content = line_bot_api.get_message_content(message_id)
    
    # 讀取影像並傳給 Vision API 進行分析
    image_data = io.BytesIO(message_content.content)
    image = vision.Image(content=image_data.read())
    response = vision_client.text_detection(image=image)
    annotations = response.text_annotations
    if annotations:
        detected_text = annotations[0].description  # 取得辨識出的文字
        print(f"Google Vision 辨識出的文字: {detected_text}")
        
        # 將辨識出的文字轉換成繁體中文並過濾非食材詞彙
        processed_text = translate_and_filter_ingredients(detected_text)
        user_id = event.source.user_id
        if processed_text:  # 確保有過濾到食材內容
            user_ingredients[user_id] = processed_text  # 儲存過濾後的食材
            print(f"處理後的食材文字: {processed_text}")
            
            # 問使用者料理問題
            question_response = ask_user_for_recipe_info()
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=question_response)
            )
        else:
            # 如果未能提取到食材，提醒使用者重新上傳圖片
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="未能識別出任何食材，請嘗試上傳另一張清晰的圖片。")
            )
    else:
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="無法辨識出任何文字。")
        )

# ChatGPT 翻譯並過濾非食材詞彙
def translate_and_filter_ingredients(detected_text):
    # 呼叫 ChatGPT 翻譯為繁體中文並過濾非食材詞彙
    prompt = f"以下是從影像中辨識出的內容：\n{detected_text}\n請將其翻譯成繁體中文，並只保留食材名稱，去除任何與食材無關的詞彙。"
    response = openai.ChatCompletion.create(
        model="gpt-4",
        messages=[
            {"role": "system", "content": "你是一個專業的翻譯助手，並且能過濾出與食材相關的內容。"},
            {"role": "user", "content": prompt}
        ]
    )
    processed_text = response.choices[0].message['content'].strip()
    return processed_text

# 問使用者料理需求
def ask_user_for_recipe_info():
    # 問題將詢問使用者他們的料理需求
    return "您今天想做甚麼樣的料理？幾人份？"

# 處理使用者對料理需求的回覆
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_id = event.source.user_id
    user_message = event.message.text
    print(f"收到訊息: {user_message}")

    if user_message == "我的最愛":
        show_favorites(event, user_id)
    elif user_message.startswith("加入我的最愛"):
        try:
            _, favorite_text = user_message.split(":", 1)
            add_to_favorites(event, user_id, favorite_text.strip())
        except ValueError:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="請使用格式 '加入我的最愛:訊息內容'")
            )
    else:
        # 取得之前處理好的食材資料，並生成對應回應
        if user_id in user_ingredients and user_ingredients[user_id]:
            ingredients = user_ingredients[user_id]
            # 呼叫 ChatGPT 生成食譜並依照使用者需求給回覆
            recipe_response = generate_recipe_response(user_message, ingredients)
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=recipe_response)
            )
        else:
            # 如果沒有儲存食材，提醒使用者上傳圖片來識別食材
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="抱歉，由於您沒有提供可以使用的食材清單，我無法為您提供確切的食譜。請上傳圖片以識別食材。")
            )

# ChatGPT 根據使用者需求和食材生成食譜回覆
def generate_recipe_response(user_message, ingredients):
    prompt = f"用戶希望做 {user_message}，可用的食材有：{ingredients}。請根據這些食材生成一個適合的食譜。"
    response = openai.ChatCompletion.create(
        model="gpt-4",
        messages=[
            {"role": "system", "content": "你是一位專業的廚師助理，會根據用戶的需求生成食譜。"},
            {"role": "user", "content": prompt}
        ]
    )
    recipe = response.choices[0].message['content'].strip()
    return recipe

# Webhook callback 處理 LINE 訊息
@app.route("/callback", methods=["POST"])
def callback():
    body = request.get_data(as_text=True)
    print(f"收到 Webhook 請求: Body: {body}")
    try:
        signature = request.headers["X-Line-Signature"]
        handler.handle(body, signature)
    except InvalidSignatureError:
        print("無效的簽名錯誤!")
        abort(400)
    return "OK"

# 健康檢查路由
@app.route("/health", methods=["GET"])
def health_check():
    return "OK", 200

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
