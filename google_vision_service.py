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
        aiplatform.init(project="fl0908", location="us-central1")
        print("Vertex AI 初始化完成！")
    except Exception as e:
        print(f"初始化 Vertex AI 失敗: {e}")
        raise
# 查詢 Dataset 標籤
def predict_with_vertex_ai(endpoint_id, project, location, instances):
    client = aiplatform.gapic.PredictionServiceClient()

    endpoint = f"projects/{project}/locations/{location}/endpoints/{endpoint_id}"

    # 格式化輸入的資料
    response = client.predict(
        endpoint=endpoint,
        instances=instances,
    )

    # 返回預測結果
    predictions = response.predictions
    return predictions
