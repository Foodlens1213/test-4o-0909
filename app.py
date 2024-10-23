from flask import Flask, request, abort, jsonify, render_template
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage, ImageMessage, FlexSendMessage, PostbackAction, PostbackEvent
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

# MySQL 資料庫連線設定
def get_db_connection():
    try:
        conn = mysql.connector.connect(
            host=os.getenv('MYSQL_HOST'),
            user=os.getenv('MYSQL_USER'),
            password=os.getenv('MYSQL_PASSWORD'),
            database=os.getenv('MYSQL_DATABASE'),
            port=os.getenv('MYSQL_PORT', 3306)
        )
        print("資料庫連線成功")
        return conn
    except mysql.connector.Error as err:
        print(f"資料庫連線錯誤: {err}")
        return None

# 儲存最愛
def save_to_favorites(user_id, favorite_text):
    conn = get_db_connection()
    if conn is None:
        print("無法連接到資料庫，無法儲存最愛")
        return
    
    try:
        cursor = conn.cursor()
        sql = "INSERT INTO favorites (user_id, favorite) VALUES (%s, %s)"
        values = (user_id, favorite_text)
        cursor.execute(sql, values)
        conn.commit()
        print(f"已成功儲存至資料庫: {user_id}, {favorite_text}")
    except mysql.connector.Error as err:
        print(f"資料庫插入錯誤: {err}")
    finally:
        cursor.close()
        conn.close()

# 查詢最愛
def get_favorites(user_id):
    conn = get_db_connection()
    if conn is None:
        print("無法連接到資料庫，無法查詢最愛")
        return []
    
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT favorite FROM favorites WHERE user_id = %s", (user_id,))
        favorites = cursor.fetchall()
        print(f"查詢結果: {favorites}")
        return [fav[0] for fav in favorites]
    except mysql.connector.Error as err:
        print(f"資料庫查詢錯誤: {err}")
        return []
    finally:
        cursor.close()
        conn.close()

# Google Cloud Vision 辨識圖片中的文字
def process_image_message(event):
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
        print(f"處理後的食材文字: {processed_text}")
        
        # 問使用者料理問題
        question_response = ask_user_for_recipe_info()
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=question_response)
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

# 處理文字訊息
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
        # 其他訊息將由 ChatGPT 處理
        try:
            response = openai.ChatCompletion.create(
                model="gpt-4",
                messages=[
                    {"role": "system", "content": "You are a helpful assistant."},
                    {"role": "user", "content": user_message},
                ]
            )
            reply_text = response.choices[0].message['content'].strip()
            print(f"ChatGPT 回覆: {reply_text}")
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
        except Exception as e:
            print(f"呼叫 ChatGPT 出錯: {e}")
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="抱歉，我暫時無法處理您的請求。"))

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

# 顯示我的最愛的網頁
@app.route("/favorites/<user_id>")
def show_favorites_web(user_id):
    favorites = get_favorites(user_id)
    if favorites:
        fav_list = [fav for fav in favorites]
    else:
        fav_list = []

    # 回傳為 JSON 給 LIFF 應用
    return jsonify({
        "user_id": user_id,
        "favorites": fav_list
    })

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
