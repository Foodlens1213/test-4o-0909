import openai
import re

# 初始化 OpenAI API 金鑰
openai.api_key = "YOUR_OPENAI_API_KEY"  # 或使用環境變數 os.getenv("OPENAI_API_KEY")

# 翻譯並過濾非食材詞彙
def translate_and_filter_ingredients(detected_labels):
    prompt = f"以下是從圖片中辨識出的物體列表：\n{', '.join(detected_labels)}\n請將其翻譯成繁體中文，並只保留與食材相關的詞彙，去除非食材的詞彙。"
    try:
        response = openai.ChatCompletion.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": "你是一個專業的翻譯助手，並且能過濾出與食材相關的內容。"},
                {"role": "user", "content": prompt}
            ]
        )
        processed_text = response.choices[0].message['content'].strip()
        return processed_text
    except Exception as e:
        print(f"翻譯和過濾過程中發生錯誤: {str(e)}")
        return None

# 生成食譜
def generate_recipe_response(user_message, ingredients):
    prompt = f"用戶希望做料理 {user_message}，可用的食材有：{ingredients}。請按照以下格式生成一個適合的食譜：\n\n料理名稱: [料理名稱]\n食材: [食材列表，單行呈現]\n食譜內容: [分步驟列點，詳述步驟]"
    try:
        response = openai.ChatCompletion.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": "你是一位專業的廚師，會根據用戶的需求生成食譜。"},
                {"role": "user", "content": prompt}
            ],
            max_tokens=800
        )
        recipe = response.choices[0].message['content'].strip()

        # 設定預設值，以防解析失敗
        dish_name = "未命名料理"
        ingredient_text = "未提供食材"
        recipe_text = "未提供食譜內容"

        # 使用正則表達式解析各部分
        dish_name_match = re.search(r"(?:料理名稱|名稱)[:：]\s*(.+)", recipe)
        ingredient_text_match = re.search(r"(?:食材|材料)[:：]\s*(.+)", recipe)
        recipe_text_match = re.search(r"(?:食譜內容|步驟)[:：]\s*((.|\n)+)", recipe)

        # 如果匹配成功，則賦值
        if dish_name_match:
            dish_name = dish_name_match.group(1).strip()
        if ingredient_text_match:
            ingredient_text = ingredient_text_match.group(1).strip()
        if recipe_text_match:
            recipe_text = recipe_text_match.group(1).strip()

        return dish_name, ingredient_text, recipe_text
    except Exception as e:
        print(f"生成食譜過程中發生錯誤: {str(e)}")
        return None, None, None
