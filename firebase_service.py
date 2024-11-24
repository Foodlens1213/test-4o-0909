import os
import firebase_admin
from firebase_admin import credentials, firestore
from google.cloud import aiplatform

# 初始化 Firebase Admin SDK 和 Firestore
def initialize_firebase():
    firebase_credentials_content = os.getenv("FIREBASE_SERVICE_ACCOUNT_KEY")
    if firebase_credentials_content:
        firebase_credentials_path = "/tmp/firebase-credentials.json"
        with open(firebase_credentials_path, "w") as f:
            f.write(firebase_credentials_content)
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = firebase_credentials_path

        cred = credentials.Certificate(firebase_credentials_path)
        firebase_admin.initialize_app(cred)
        return firestore.client()
    else:
        print("Firebase 金鑰未正確設置，請檢查環境變數")
        return None

# 儲存最愛食譜到 Firestore
def save_recipe_to_db(db, user_id, dish_name, recipe_text, ingredient_text):
    try:
        doc_ref = db.collection('recipes').document()
        doc_ref.set({
            'user_id': user_id,
            'dish': dish_name,
            'ingredient': ingredient_text,
            'recipe': recipe_text
        })
        return doc_ref.id
    except Exception as e:
        print(f"Firestore 插入錯誤: {e}")
        return None

# 從 Firestore 根據 recipe_id 查詢食譜
def get_recipe_from_db(db, recipe_id):
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

# 從 Firestore 獲取用戶的收藏食譜
def get_user_favorites(db, user_id):
    try:
        favorites_ref = db.collection('favorites').where('user_id', '==', user_id)
        docs = favorites_ref.stream()
        return [{'id': doc.id, **doc.to_dict()} for doc in docs]
    except Exception as e:
        print(f"Firestore 查詢錯誤: {e}")
        return None

# 從 Firestore 刪除指定的收藏食譜
def delete_favorite_from_db(db, recipe_id):
    try:
        print(f"嘗試刪除的食譜 ID: {recipe_id}")
        
        # 檢查 recipes 集合中是否存在指定的 recipe_id
        doc_ref = db.collection('recipes').document(recipe_id)
        if doc_ref.get().exists:
            print("找到對應的食譜文檔，繼續查詢 favorites 集合...")
            
            # 查詢 favorites 集合中 recipe_id 相符的文檔
            favorites_ref = db.collection('favorites').where('recipe_id', '==', recipe_id).stream()
            deleted = False
            for favorite in favorites_ref:
                # 刪除 favorites 集合中的文檔
                db.collection('favorites').document(favorite.id).delete()
                print(f"已刪除 favorites 集合中的文檔: {favorite.id}")
                deleted = True
            
            if deleted:
                return True
            else:
                print("未找到與該 recipe_id 相符的收藏文檔")
                return False
        else:
            print("找不到對應的食譜文檔")
            return False
    except Exception as e:
        print(f"刪除文檔時發生錯誤: {e}")
        return False
