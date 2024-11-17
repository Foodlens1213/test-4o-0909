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
def save_recipe_to_db(user_id, dish_name, recipe_text, ingredient_text):
    try:
        # 手動生成一個新的 DocumentReference，這樣可以提前獲取 ID
        doc_ref = db.collection('recipes').document()
        doc_ref.set({
            'user_id': user_id,
            'dish': dish_name,
            'ingredient': ingredient_text,
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
        # 查詢 favorites 集合，篩選符合 user_id 的食譜
        favorites_ref = db.collection('favorites').where('user_id', '==', user_id)
        docs = favorites_ref.stream()
        favorites = [{'id': doc.id, **doc.to_dict()} for doc in docs]
        return jsonify(favorites), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

def generate_recipe_response(user_message, ingredients):
    # 增加針對 iCook 食譜的要求
    prompt = (
        f"用戶希望做 {user_message}，可用的食材有：{ingredients}。\n"
        "請參考 https://icook.tw/ 上的所有食譜，並按照以下格式生成兩道適合的食譜：\n\n"
        "料理名稱: [料理名稱]\n"
        "食材: [食材列表，單行呈現]\n"
        "食譜內容: [分步驟列點，詳述步驟]\n"
        "來源: [iCook 來源鏈接]"
    )

    # 從 ChatGPT 獲取回應
    response = openai.ChatCompletion.create(
        model="gpt-4",
        messages=[
            {"role": "system", "content": "你是一位專業的廚師，會根據用戶的需求生成食譜，且所有食譜需來自 iCook。"},
            {"role": "user", "content": prompt}
        ],
        max_tokens=1000  # 增加 max_tokens 以確保足夠空間生成兩道完整食譜
    )
    recipe = response.choices[0].message['content'].strip()
    print(f"ChatGPT 返回的內容: {recipe}")  # 除錯：打印原始回應以進行檢查

    # 設定預設值，以防解析失敗
    dish_name = "未命名料理"
    ingredient_text = "未提供食材"
    recipe_text = "未提供食譜內容"

    # 使用更嚴格的正則表達式解析各部分
    dish_name_match = re.search(r"(?:食譜名稱|名稱)[:：]\s*(.+)", recipe)
    ingredient_text_match = re.search(r"(?:食材|材料)[:：]\s*(.+)", recipe)
    recipe_text_match = re.search(r"(?:食譜內容|步驟)[:：]\s*((.|\n)+)", recipe)

    # 如果匹配成功，則賦值
    if dish_name_match:
        dish_name = dish_name_match.group(1).strip()
    if ingredient_text_match:
        ingredient_text = ingredient_text_match.group(1).strip()
    if recipe_text_match:
        recipe_text = recipe_text_match.group(1).strip()

    # 除錯：打印解析出的值
    print(f"解析出的料理名稱: {dish_name}")
    print(f"解析出的食材: {ingredient_text}")
    print(f"解析出的食譜內容: {recipe_text}")

    return dish_name, ingredient_text, recipe_text


import re
def clean_text(text):
    # 去除無效字符和表情符號
    return re.sub(r'[^\w\s,.!?]', '', text)
def create_flex_message(recipe_text, user_id, dish_name, ingredient_text, ingredients, recipe_number):
    recipe_id = save_recipe_to_db(user_id, dish_name, recipe_text, ingredient_text)
    if isinstance(ingredients, list):
        ingredients_str = ','.join(ingredients)
    else:
        ingredients_str = str(ingredients)

    # 設置 Flex Message 結構
    bubble = {
        "type": "bubble",
        "body": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {
                    "type": "text",
                    "text": f"料理名稱 {recipe_number}：{dish_name}",
                    "wrap": True,
                    "weight": "bold",
                    "size": "xl"
                },
                {
                    "type": "text",
                    "text": f"食材：{ingredient_text}",
                    "wrap": True,
                    "margin": "md",
                    "size": "sm"
                },
                {
                    "type": "text",
                    "text": "食譜內容：",
                    "wrap": True,
                    "weight": "bold",
                    "size": "lg",
                    "margin": "md"
                },
                {
                    "type": "text",
                    "text": recipe_text if recipe_text else "食譜內容缺失",
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
    return bubble

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
            print(f"辨識到的食材: {detected_labels}")  # 在 log 中顯示食材
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
    return "您今天想做甚麼樣的料理？幾道菜？"

# 處理使用者需求
@handler.add(PostbackEvent)
def handle_postback(event):
    data = event.postback.data
    params = dict(x.split('=') for x in data.split('&'))
    action = params.get('action')
    user_id = params.get('user_id') or event.source.user_id  # 確保 user_id 存在

    if action == 'new_recipe':
        # 回覆 "沒問題，請稍後~"
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="沒問題，請稍後~")
        )

        ingredients = params.get('ingredients')
        dish_name, ingredient_text, recipe_text = generate_recipe_response("新的食譜", ingredients)

        # 建立並發送 Flex Message
        flex_message = FlexSendMessage(
            alt_text="您的新食譜",
            contents=create_flex_message(recipe_text, user_id, dish_name, ingredient_text, ingredients, 1)
        )
        line_bot_api.push_message(user_id, flex_message)

        # 發送 YouTube 和 iCook 搜尋連結作為一般訊息
        youtube_url = f"https://www.youtube.com/results?search_query={dish_name.replace(' ', '+')}"
        icook_url = f"https://icook.tw/search/{dish_name.replace(' ', '%20')}"
        
        line_bot_api.push_message(user_id, [
            TextSendMessage(text=f"🔍 iCook 搜尋結果: {icook_url}"),
            TextSendMessage(text=f"🎥 YouTube 搜尋結果: {youtube_url}")
        ])

    elif action == 'save_favorite':
        recipe_id = params.get('recipe_id')

        recipe = get_recipe_from_db(recipe_id)
        if recipe:
            try:
                db.collection('favorites').add({
                    'user_id': user_id,
                    'dish': recipe['dish'],
                    'ingredient': recipe['ingredient'],
                    'recipe': recipe['recipe'],
                    'recipe_id': recipe_id
                })
                line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(text="已成功將食譜加入我的最愛!")
                )
            except Exception as e:
                print(f"儲存最愛時發生錯誤: {e}")
                line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(text="抱歉，儲存過程中發生錯誤。")
                )
        else:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="找不到該食譜，無法加入我的最愛")
            )

    else:
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="抱歉，我不太明白您的需求。")
        )



def generate_multiple_recipes(dish_count, ingredients):
    recipes = []
    existing_dishes = set()  # 用於追踪生成的菜名，避免重複

    for _ in range(dish_count):
        while True:
            # 生成食譜
            dish_name, ingredient_text, recipe_text = generate_recipe_response("", ingredients)

            # 如果食譜不重複，則加入清單並跳出迴圈
            if dish_name not in existing_dishes:
                recipes.append((dish_name, ingredient_text, recipe_text))
                existing_dishes.add(dish_name)
                break
            else:
                print("生成的食譜重複，重新生成...")

    return recipes


# 將中文數字轉換為阿拉伯數字的函數
def chinese_to_digit(user_message):
    chinese_digits = {'零': 0, '一': 1, '二': 2, '兩': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7, '八': 8, '九': 9}
    chinese_num = re.search(r"[零一二兩三四五六七八九]", user_message)
    if chinese_num:
        return chinese_digits[chinese_num.group()]
    return None

# 更新 handle_message 函數
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_id = event.source.user_id
    user_message = event.message.text

    if "道" in user_message:
        # 從使用者訊息提取數字，表示需要幾道菜
        dish_count = None
        # 優先檢查阿拉伯數字
        if re.search(r"\d+", user_message):
            dish_count = int(re.search(r"\d+", user_message).group())
        # 若無阿拉伯數字，檢查漢字
        else:
            dish_count = chinese_to_digit(user_message)  # 傳入 user_message
        # 預設為1道菜，如果數字解析成功，則使用提取到的數字
        dish_count = dish_count if dish_count is not None else 1
        ingredients = user_ingredients.get(user_id, None)

        if ingredients:
            # 根據需要的數量生成多道料理
            recipes = generate_multiple_recipes(dish_count, ingredients)

            # 準備多頁式回覆
            flex_bubbles = [
                create_flex_message(recipe_text, user_id, dish_name, ingredient_text, ingredients, i + 1)
                for i, (dish_name, ingredient_text, recipe_text) in enumerate(recipes)
            ]
            carousel = {
                "type": "carousel",
                "contents": flex_bubbles
            }
            line_bot_api.reply_message(
                event.reply_token, 
                FlexSendMessage(alt_text="您的多道食譜", contents=carousel)
            )
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
