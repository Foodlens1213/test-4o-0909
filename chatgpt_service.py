import openai
import re
import os
from dotenv import load_dotenv

# 初始化 OpenAI API 金鑰
load_dotenv()
openai.api_key = os.getenv("OPENAI_API_KEY") # 使用環境變數 os.getenv("OPENAI_API_KEY")

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
def generate_recipe_response(dish_type, dish_count, ingredients):
    # 動態生成 prompt
    prompt = (
        f"用戶希望做 {dish_type} 料理，共 {dish_count} 道菜，並指定使用以下食材：{ingredients}。\n"
        f"請根據需求生成一個詳細的食譜，並按照以下格式輸出：\n\n"
        f"料理名稱: [料理名稱，請與主題相關並避免重複]\n"
        f"食材: [食材列表，單行呈現]\n"
        f"食譜內容: [分步驟列點，詳細描述每個步驟]\n"
        f"注意：生成的料理應符合主題 {dish_type}，並根據指定的食材產出食譜。\n"
        f"請確保生成的食譜是從 https://icook.tw/ 中的料理。\n"
    )

    try:
        # 調用 OpenAI API
        response = openai.ChatCompletion.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": "你是一位專業的廚師，專注於為用戶創建食譜。"},
                {"role": "user", "content": prompt}
            ],
            max_tokens=800
        )
        recipe = response.choices[0].message['content'].strip()

        # 預設值防止解析錯誤
        dish_name = "未命名料理"
        ingredient_text = "未提供食材"
        recipe_text = "未提供食譜內容"

        # 正則解析返回的食譜內容
        dish_name_match = re.search(r"(?:料理名稱|名稱)[:：]\s*(.+)", recipe)
        ingredient_text_match = re.search(r"(?:食材|材料)[:：]\s*(.+)", recipe)
        recipe_text_match = re.search(r"(?:食譜內容|步驟)[:：]\s*((.|\n)+)", recipe)

        # 賦值解析出的內容
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
