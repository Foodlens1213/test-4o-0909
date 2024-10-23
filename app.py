from flask import Flask, request, abort, jsonify
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage, ImageMessage, FlexSendMessage, PostbackEvent
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
        return conn
    except mysql.connector.Error as err:
        print(f"資料庫連線錯誤: {err}")
        return None

# 儲存最愛食譜
def save_to_favorites(user_id, dish_name, recipe_text, video_link):
    conn = get_db_connection()
    if conn is None:
        print("無法連接到資料庫，無法儲存最愛")
        return
    
    try:
        cursor = conn.cursor()
        sql = "INSERT INTO favorites (user_id, dish, recipe, link) VALUES (%s, %s, %s, %s)"
        values = (user_id, dish_name, recipe_text, video_link)
        cursor.execute(sql, values)
        conn.commit()
        print(f"已成功儲存至資料庫: {user_id}, {dish_name}, {recipe_text}, {video_link}")
    except mysql.connector.Error as err:
        print(f"資料庫插入錯誤: {err}")
    finally:
        cursor.close()
        conn.close()

# ChatGPT 根據使用者需求和食材生成食譜回覆，並限制在 300 字內
def generate_recipe_response_with_video(user_message, ingredients):
    prompt = f"用戶希望做 {user_message}，可用的食材有：{ingredients}。請根據這些食材生成一個適合的食譜，字數限制在300字以內，並附上一個相關的 YouTube 食譜影片連結。"
    response = openai.ChatCompletion.create(
        model="gpt-4",
        messages=[
            {"role": "system", "content": "你是一位專業的廚師助理，會根據用戶的需求生成食譜，並提供 YouTube 影片連結。"},
            {"role": "user", "content": prompt}
        ],
        max_tokens=300  # 限制 ChatGPT 回應的字數
    )
    recipe = response.choices[0].message['content'].strip()
    return recipe

# 建立多頁式訊息，新增「查看影片」按鈕
def create_flex_message(recipe_text, video_url, user_id, dish_name):
    bubble = {
        "type": "bubble",
        "body": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {
                    "type": "text",
                    "text": "您的食譜：",
                    "wrap": True,
                    "weight": "bold",
                    "size": "xl"
                },
                {
                    "type": "text",
                    "text": recipe_text,
                    "wrap": True,
                    "margin": "md",
                    "size": "sm"
                }
            ]
        },
        "footer": {
            "type": "box",
            "layout": "vertical",
            "spacing": "sm",
            "contents": [
                {
                    "type": "button",
                    "action": {
                        "type": "uri",
                        "label": "查看影片",
                        "uri": video_url  # 這裡是 YouTube 影片連結
                    },
                    "color": "#FF4500",
                    "style": "primary"
                },
                {
                    "type": "button",
                    "action": {
                        "type": "postback",
                        "label": "有沒有其他的食譜",
                        "data": f"action=new_recipe&user_id={user_id}&recipe_text={recipe_text}"
                    },
                    "color": "#1DB446",
                    "style": "primary"
                },
                {
                    "type": "button",
                    "action": {
                        "type": "postback",
                        "label": "我想辨識新的一張圖片",
                        "data": "action=new_image"
                    },
                    "color": "#FF4500",
                    "style": "primary"
                },
                {
                    "type": "button",
                    "action": {
                        "type": "postback",
                        "label": "把這個食譜加入我的最愛",
                        "data": f"action=save_favorite&user_id={user_id}&dish={dish_name}&recipe_text={recipe_text}&video_link={video_url}"
                    },
                    "color": "#0000FF",
                    "style": "primary"
                }
            ]
        }
    }

    carousel = {
        "type": "carousel",
        "contents": [bubble]
    }
    return FlexSendMessage(alt_text="您的食譜", contents=carousel)

# 處理圖片訊息，進行 Google Cloud Vision 的物體偵測（Label Detection）
@handler.add(MessageEvent, message=ImageMessage)
def handle_image_message(event):
    message_id = event.message.id
    message_content = line_bot_api.get_message_content(message_id)
    
    # 讀取影像並傳給 Vision API 進行物體偵測
    image_data = io.BytesIO(message_content.content)
    image = vision.Image(content=image_data.read())
    
    try:
        response = vision_client.label_detection(image=image)
        labels = response.label_annotations
        
        if labels:
            # 取得辨識出的標籤（物體名稱）
            detected_labels = [label.description for label in labels]
            print(f"Google Vision 辨識出的標籤: {detected_labels}")
            
            # 將標籤傳送給 ChatGPT 進行繁體中文轉換及過濾非食材詞彙
            processed_text = translate_and_filter_ingredients(detected_labels)
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
            # 無法辨識出任何物體
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="無法辨識出任何物體，請確保圖片中的食材明顯可見。")
            )
    except Exception as e:
        # 回傳 Google Vision API 的錯誤資訊
        print(f"Google Vision API 錯誤: {str(e)}")
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=f"圖片辨識過程中發生錯誤: {str(e)}")
        )

# ChatGPT 翻譯並過濾非食材詞彙
def translate_and_filter_ingredients(detected_labels):
    # 呼叫 ChatGPT 翻譯為繁體中文並過濾非食材詞彙
    prompt = f"以下是從圖片中辨識出的物體列表：\n{', '.join(detected_labels)}\n請將其翻譯成繁體中文，並只保留與食材相關的詞彙，去除非食材的詞彙。"
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
    return "您今天想做甚麼樣的料理？幾人份？"

# 處理使用者需求
@handler.add(PostbackEvent)
def handle_postback(event):
    data = event.postback.data
    params = dict(x.split('=') for x in data.split('&'))
    action = params.get('action')
    user_id = params.get('user_id')

    if action == 'new_recipe':
        ingredients = params.get('ingredients')
        new_recipe = generate_recipe_response_with_video("新的食譜", ingredients)
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=f"新的食譜：\n{new_recipe}")
        )
    elif action == 'new_image':
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="請上傳一張新圖片來辨識食材。")
        )
    elif action == 'save_favorite':
        # 從 postback 參數中取得食譜相關資訊
        recipe_text = params.get('recipe_text')
        dish_name = params.get('dish')
        video_link = params.get('video_link')

        # 將食譜存入資料庫
        save_to_favorites(user_id, dish_name, recipe_text, video_link)
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="已將此食譜加入您的最愛。")
        )

# 處理文字訊息，並生成多頁式食譜回覆
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_id = event.source.user_id
    user_message = event.message.text
    
    # 假設使用者回答了關於料理需求的問題
    if "份" in user_message or "人" in user_message:
        ingredients = user_ingredients.get(user_id, None)
        if ingredients:
            # 根據使用者的需求和先前辨識的食材生成食譜
            recipe_response = generate_recipe_response_with_video(user_message, ingredients)
            video_url = "https://www.youtube.com/results?search_query=recipe"  # 替換為 ChatGPT 生成的影片連結
            flex_message = create_flex_message(recipe_response, video_url, user_id, "焗烤料理")  # 假設 dish 名稱為 "焗烤料理"
            line_bot_api.reply_message(event.reply_token, flex_message)
        else:
            # 如果沒有已辨識的食材，回應提示使用者上傳圖片
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="請先上傳圖片來辨識食材。")
            )
    else:
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="請告訴我您想要做什麼料理及份數。")
        )

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
    except Exception as e:
        # 捕捉並打印完整的錯誤訊息
        print(f"發生錯誤: {str(e)}")
        abort(500)  # 回傳 500 錯誤給客戶端
    return "OK"

# 健康檢查路由
@app.route("/health", methods=["GET"])
def health_check():
    return "OK", 200

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
