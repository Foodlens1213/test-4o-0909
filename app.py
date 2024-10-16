# -*- coding: utf-8 -*-
from flask import Flask, request, abort, jsonify
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage, PostbackEvent
import os
import mysql.connector

app = Flask(__name__)

# LINE Bot API settings
line_bot_api = LineBotApi(os.getenv("LINE_CHANNEL_ACCESS_TOKEN"))
handler = WebhookHandler(os.getenv("LINE_CHANNEL_SECRET"))

# MySQL connection
def get_db_connection():
    try:
        conn = mysql.connector.connect(
            host=os.getenv('MYSQL_HOST'),
            user=os.getenv('MYSQL_USER'),
            password=os.getenv('MYSQL_PASSWORD'),
            database=os.getenv('MYSQL_DATABASE'),
            port=os.getenv('MYSQL_PORT', 3306)  # 預設端口3306，若不同可修改
        )
        print("資料庫連線成功")
        return conn
    except mysql.connector.Error as err:
        print(f"資料庫連線錯誤: {err}")
        return None

# Save to favorites
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

# 處理文字訊息
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_id = event.source.user_id
    user_message = event.message.text
    print(f"收到訊息: {user_message}")

    if user_message == "我的最愛":
        favorites = get_favorites(user_id)
        if favorites:
            reply_text = "您的最愛：\n" + "\n".join(favorites)
        else:
            reply_text = "您的最愛清單是空的。"
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=reply_text)
        )
    elif user_message.startswith("加入我的最愛:"):
        _, favorite_text = user_message.split(":", 1)
        save_to_favorites(user_id, favorite_text.strip())
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=f"已將訊息新增至最愛：{favorite_text.strip()}")
        )
    else:
        reply_text = "抱歉，我不明白您的訊息。"
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=reply_text)
        )

# Postback handling
@handler.add(PostbackEvent)
def handle_postback(event):
    data = event.postback.data
    user_id = event.source.user_id
    print(f"收到 Postback: {data}")

    if data.startswith("action=add_favorite"):
        _, favorite_text = data.split("&message=")
        save_to_favorites(user_id, favorite_text)
        line_bot_api.reply_message(
            event.reply_token, 
            TextSendMessage(text=f"已將訊息新增至最愛：{favorite_text}")
        )

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

# 健康檢查
@app.route("/health", methods=["GET"])
def health_check():
    return "OK", 200

# Webhook callback
@app.route("/callback", methods=["POST"])
def callback():
    body = request.get_data(as_text=True)
    print(f"Received Webhook request: Body: {body}")

    try:
        signature = request.headers["X-Line-Signature"]
        handler.handle(body, signature)
    except InvalidSignatureError:
        print("Invalid Signature Error!")
        abort(400)

    return "OK"

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
