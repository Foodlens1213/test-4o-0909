@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_message = event.message.text

    # 檢查是否為觸發多頁訊息的關鍵字
    if user_message in ['食譜推薦', '料理推薦']:  # 關鍵字列表，保持由LINE自動回覆處理
        return  # 當使用者傳送關鍵字時，讓LINE的自動回覆處理

    # 使用 ChatGPT 生成回覆
    try:
        print(f"Sending message to ChatGPT: {user_message}")  # 日誌記錄用戶輸入的訊息
        response = openai.ChatCompletion.create(
            model="gpt-4",  # 使用 GPT-4 模型，或 "gpt-3.5-turbo"
            messages=[
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": user_message},
            ]
        )
        reply_text = response.choices[0].message['content'].strip()
        print(f"ChatGPT response: {reply_text}")  # 日誌記錄 ChatGPT 回應
    except Exception as e:
        print(f"Error calling ChatGPT: {e}")  # 日誌記錄錯誤
        reply_text = "抱歉，我暫時無法處理您的請求。"

    # 將回應發送給使用者
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=reply_text)
    )
