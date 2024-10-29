from flask import Flask, request, abort, jsonify, render_template
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage, ImageMessage, FlexSendMessage, PostbackEvent
import openai
import os
from google.cloud import vision
from dotenv import load_dotenv
import io
import firebase_admin
from firebase_admin import credentials, firestore

# 載入環境變數
load_dotenv()
app = Flask(__name__)

# 初始化 Firebase Admin SDK 和 Firestore
firebase_credentials_content = os.getenv("FIREBASE_SERVICE_ACCOUNT_KEY")
if firebase_credentials_content:
    firebase_credentials_path = "/tmp/firebase-credentials.json"
    with open(firebase_credentials_path, "w") as f:
        f.write(firebase_credentials_content)
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = firebase_credentials_path

    cred = credentials.Certificate(firebase_credentials_path)
    firebase_admin.initialize_app(cred)
    db = firestore.client()
else:
    print("Firebase 金鑰未正確設置，請檢查環境變數")

# 初始化 Google Cloud Vision API 客戶端
google_credentials_content = os.getenv("GOOGLE_APPLICATION_CREDENTIALS_CONTENT")
if google_credentials_content:
    credentials_path = "/tmp/google-credentials.json"
    with open(credentials_path, "w") as f:
        f.write(google_credentials_content)
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = credentials_path
vision_client = vision.ImageAnnotatorClient()

# LINE Bot API 和 Webhook 設定
line_bot_api = LineBotApi(os.getenv("LINE_CHANNEL_ACCESS_TOKEN"))
handler = WebhookHandler(os.getenv("LINE_CHANNEL_SECRET"))

# OpenAI API 金鑰
openai.api_key = os.getenv("OPENAI_API_KEY")

# 儲存處理後的食材資料（供後續使用）
user_ingredients = {}

# 儲存最愛食譜到 Firebase Firestore
def save_recipe_to_db(user_id, dish_name, recipe_text):
    try:
        # 手動生成一個新的 DocumentReference，這樣可以提前獲取 ID
        doc_ref = db.collection('recipes').document()
        doc_ref.set({
            'user_id': user_id,
            'dish': dish_name,
            'recipe': recipe_text
        })
        return doc_ref.id  # 返回生成的文檔 ID
    except Exception as e:
        print(f"Firestore 插入錯誤: {e}")
        return None

# 從 Firebase Firestore 根據 recipe_id 查詢食譜
def get_recipe_from_db(recipe_id):
    try:
        recipe_doc = db.collection('recipes').document(recipe_id).get()
        if recipe_doc.exists:
            return recipe_doc.to_dict()
        else:
            print("找不到對應的食譜")
            return None
    except Exception as e:
        print(f"Firestore 查詢錯誤: {e}")
        return None

# 顯示收藏的食譜（前端頁面）
@app.route('/favorites')
def favorites_page():
    return render_template('favorites.html')

# 從 Firestore 獲取用戶的收藏食譜 (API)
@app.route('/api/favorites', methods=['GET'])
def get_user_favorites():
    user_id = request.args.get('user_id')
    if not user_id:
        return jsonify({'error': 'Missing user_id'}), 400

    try:
        recipes_ref = db.collection('recipes').where('user_id', '==', user_id)
        docs = recipes_ref.stream()
        favorites = [doc.to_dict() for doc in docs]
        return jsonify(favorites), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

def generate_recipe_response(user_message, ingredients):
    prompt = f"用戶希望做 {user_message}，可用的食材有：{ingredients}。請按照以下格式生成一個適合的食譜：\n\n食譜名稱: [食譜名稱]\n食材: [食材列表]\n步驟: [具體步驟]，字數限制在300字以內。"
    response = openai.ChatCompletion.create(
        model="gpt-4",
        messages=[
            {"role": "system", "content": "你是一位專業的廚師助理，會根據用戶的需求生成食譜。"},
            {"role": "user", "content": prompt}
        ],
        max_tokens=500
    )
    recipe = response.choices[0].message['content'].strip()

    # 打印返回的完整內容，便於調試
    print(f"ChatGPT 返回的內容: {recipe}")

    dish_name = None
    recipe_text = None

    # 修改解析邏輯，支持新的標籤
    try:
        recipe_parts = recipe.split("\n\n")  # 分割段落
        for part in recipe_parts:
            if "食譜名稱:" in part:
                dish_name = part.replace("食譜名稱:", "").strip()
            elif "步驟:" in part:
                recipe_text = part.replace("步驟:", "").strip()

    except Exception as e:
        print(f"解析 ChatGPT 回應失敗: {e}")

    # 如果沒有解析到，設置默認值
    if not dish_name:
        dish_name = "未命名料理"
    if not recipe_text:
        recipe_text = "未提供食譜內容"

    print(f"解析出的料理名稱: {dish_name}")
    print(f"解析出的食譜內容: {recipe_text}")

    return dish_name, recipe_text


import re
def clean_text(text):
    # 去除無效字符和表情符號
    return re.sub(r'[^\w\s,.!?]', '', text)
# 建立多頁訊息，按鈕點擊後會變更顏色並回應
def create_flex_message(recipe_text, user_id, dish_name, ingredients):
    recipe_id = save_recipe_to_db(user_id, dish_name, recipe_text)

    # 確保 ingredients 是一個列表，並將其轉換為字符串
    if isinstance(ingredients, list):
        ingredients_str = ','.join(ingredients)
    else:
        ingredients_str = str(ingredients)

    # 修改 bubble 結構，將料理名稱和食譜內容正確地顯示
    bubble = {
        "type": "bubble",
        "body": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {
                    "type": "text",
                    "text": f"料理名稱：{dish_name}",  # 顯示料理名稱
                    "wrap": True,
                    "weight": "bold",
                    "size": "xl"
                },
                {
                    "type": "text",
                    "text": "您的食譜：",
                    "wrap": True,
                    "weight": "bold",
                    "size": "lg",
                    "margin": "md"
                },
                {
                    "type": "text",
                    "text": recipe_text[:1000] if recipe_text else "食譜內容缺失",  # 顯示食譜內容
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
                        "type": "postback",
                        "label": "有沒有其他的食譜",
                        "data": f"action=new_recipe&user_id={user_id}&ingredients={ingredients_str}"
                    },
                    "color": "#474242",
                    "style": "primary",
                    "height": "sm"
                },
                {
                    "type": "button",
                    "action": {
                        "type": "postback",
                        "label": "把這個食譜加入我的最愛",
                        "data": f"action=save_favorite&recipe_id={recipe_id}"
                    },
                    "color": "#474242",
                    "style": "primary",
                    "height": "sm"
                }
            ]
        },
        "styles": {
            "footer": {
                "separator": True
            }
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

    image_data = io.BytesIO(message_content.content)
    image = vision.Image(content=image_data.read())

    try:
        response = vision_client.label_detection(image=image)
        labels = response.label_annotations

        if labels:
            detected_labels = [label.description for label in labels]
            processed_text = translate_and_filter_ingredients(detected_labels)
            user_id = event.source.user_id
            if processed_text:
                user_ingredients[user_id] = processed_text
                question_response = ask_user_for_recipe_info()
                line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(text=question_response)
                )
            else:
                line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(text="未能識別出任何食材，請嘗試上傳另一張清晰的圖片。")
                )
        else:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="無法辨識出任何物體，請確保圖片中的食材明顯可見。")
            )
    except Exception as e:
        print(f"Google Vision API 錯誤: {str(e)}")
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=f"圖片辨識過程中發生錯誤: {str(e)}")
        )

# ChatGPT 翻譯並過濾非食材詞彙
def translate_and_filter_ingredients(detected_labels):
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
        # 回覆"沒問題，請稍後~"
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="沒問題，請稍後~")
        )
        # 生成新的食譜
        ingredients = params.get('ingredients')
        new_recipe = generate_recipe_response("新的食譜", ingredients)
        flex_message = create_flex_message(new_recipe, user_id, "新食譜", ingredients)
        line_bot_api.push_message(user_id, flex_message)

    elif action == 'new_image':
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="請上傳一張新圖片來辨識食材。")
        )

    elif action == 'save_favorite':
        recipe_id = params.get('recipe_id')
        recipe = get_recipe_from_db(recipe_id)
        save_recipe_to_db(user_id, recipe['dish'], recipe['recipe'])
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="已加入我的最愛~")
        )

# 處理文字訊息，並生成多頁式食譜回覆
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_id = event.source.user_id
    user_message = event.message.text

    if "份" in user_message or "人" in user_message:
        ingredients = user_ingredients.get(user_id, None)
        if ingredients:
            # 生成料理名稱和食譜內容
            dish_name, recipe_response = generate_recipe_response(user_message, ingredients)
            # 使用生成的料理名稱而非硬編碼的值
            flex_message = create_flex_message(recipe_response, user_id, dish_name, ingredients)
            line_bot_api.reply_message(event.reply_token, flex_message)
        else:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="請先上傳圖片來辨識食材。")
            )
    else:
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="請告訴我您想要做什麼料理及份數。")
        )

# 顯示特定食譜的詳細內容 (供 "查看更多" 使用)
@app.route('/api/favorites/<recipe_id>', methods=['GET'])
def get_recipe_detail(recipe_id):
    try:
        recipe_doc = db.collection('recipes').document(recipe_id).get()
        if recipe_doc.exists:
            return jsonify(recipe_doc.to_dict()), 200
        else:
            return jsonify({'error': 'Recipe not found'}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# 刪除食譜 (供 "刪除" 使用)
@app.route('/api/favorites', methods=['DELETE'])
def delete_favorite():
    recipe_id = request.args.get('recipe_id')
    if not recipe_id:
        return jsonify({'error': 'Missing recipe_id'}), 400

    try:
        db.collection('recipes').document(recipe_id).delete()
        return jsonify({'status': 'success'}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

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
        print(f"發生錯誤: {str(e)}")
        abort(500)
    return "OK"

# 健康檢查路由
@app.route("/health", methods=["GET"])
def health_check():
    return "OK", 200

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
