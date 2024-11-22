import os
from google.cloud import vision
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
