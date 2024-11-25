import os
from google.cloud import vision
from google.cloud import aiplatform
import io

# 初始化 Google Cloud Vision API 客戶端
def initialize_vision_client():
    google_credentials_content = os.getenv("GOOGLE_APPLICATION_CREDENTIALS_CONTENT")
    if google_credentials_content:
        credentials_path = "/tmp/google-credentials.json"
        with open(credentials_path, "w") as f:
            f.write(google_credentials_content)
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = credentials_path
    return vision.ImageAnnotatorClient()

# 使用 Vision API 進行 Label Detection
def detect_labels(image_content):
    try:
        client = initialize_vision_client()
        image = vision.Image(content=image_content)
        response = client.label_detection(image=image)
        if response.error.message:
            raise Exception(f"Vision API Error: {response.error.message}")
        return [label.description for label in response.label_annotations]
    except Exception as e:
        print(f"Google Vision API 錯誤: {str(e)}")
        return None
# 初始化 Vertex AI 環境
def initialize_vertex_ai():
    try:
        # 設定您的專案 ID 和地區
        aiplatform.init(project="YOUR_PROJECT_ID", location="us-central1")
        print("Vertex AI 初始化完成！")
    except Exception as e:
        print(f"初始化 Vertex AI 失敗: {e}")
        raise
# 查詢 Dataset 標籤
def fetch_labels_from_vertex(dataset_id):
    try:
        # 加載指定的 Dataset
        dataset = aiplatform.ImageDataset(
            dataset_name=f"projects/YOUR_PROJECT_ID/locations/us-central1/datasets/{dataset_id}"
        )
        print(f"正在查詢 Dataset：{dataset_id}")

        # 列出 Dataset 中的所有圖片
        data_items = dataset.list_data_items()
        print(f"找到 {len(data_items)} 個圖片項目。")

        # 將標籤提取出來
        labels_data = {}
        for item in data_items:
            item_id = item.name.split("/")[-1]  # 獲取圖片 ID
            labels = item.labels  # 獲取標籤
            metadata = item.metadata.get("image_url", "未知圖片 URL")  # 可選的圖片 URL
            labels_data[item_id] = {
                "labels": labels,
                "metadata": metadata,
            }
            print(f"圖片 ID: {item_id}, 標籤: {labels}, URL: {metadata}")

        return labels_data
    except Exception as e:
        print(f"查詢 Dataset 標籤失敗: {e}")
        raise
