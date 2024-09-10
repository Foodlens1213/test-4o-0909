from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage, ImageMessage
import openai
import os
from dotenv import load_dotenv
import requests
import base64
from clarifai_grpc.grpc.api import resources_pb2, service_pb2, service_pb2_grpc
from clarifai_grpc.grpc.api.status import status_code_pb2
import grpc
from google.protobuf.json_format import MessageToDict

# Load environment variables from .env file
load_dotenv()

app = Flask(__name__)

# LINE Bot API and Webhook settings
line_bot_api = LineBotApi(os.getenv("LINE_CHANNEL_ACCESS_TOKEN"))
handler = WebhookHandler(os.getenv("LINE_CHANNEL_SECRET"))

# OpenAI API key
openai.api_key = os.getenv("OPENAI_API_KEY")

# Clarifai API settings
CLARIFAI_API_KEY = os.getenv("CLARIFAI_API_KEY")
CLARIFAI_CHANNEL = grpc.secure_channel('api.clarifai.com', grpc.ssl_channel_credentials())
CLARIFAI_STUB = service_pb2_grpc.V2Stub(CLARIFAI_CHANNEL)
CLARIFAI_METADATA = (('authorization', f'Key {CLARIFAI_API_KEY}'),)

# Home route for testing
@app.route("/")
def home():
    return "Hello! This is your LINE Bot server."
    
@app.route("/callback", methods=["POST"])
def callback():
    # get X-Line-Signature header value
    signature = request.headers["X-Line-Signature"]

    # get request body as text
    body = request.get_data(as_text=True)
    app.logger.info(f"Request body: {body}")

    # handle webhook body
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)

    return "OK"

# Function to recognize image with Clarifai API
def recognize_image_with_clarifai(image_bytes):
    request = service_pb2.PostModelOutputsRequest(
        model_id='aaa03c23b3724a16a56b629203edc62c',  # General model
        inputs=[
            resources_pb2.Input(
                data=resources_pb2.Data(image=resources_pb2.Image(base64=image_bytes))
            )
        ]
    )
    response = CLARIFAI_STUB.PostModelOutputs(request, metadata=CLARIFAI_METADATA)
    
    if response.status.code != status_code_pb2.SUCCESS:
        raise Exception(f"Clarifai API call failed: {response.status.description}")
    
    output = response.outputs[0]
    return MessageToDict(output)

# Handle text messages
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_message = event.message.text

    # Send the user's message to OpenAI GPT
    response = openai.ChatCompletion.create(
        model="gpt-4",  
        messages=[
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": user_message},
        ]
    )
    reply_text = response.choices[0].message['content'].strip()

    # Send response back to the user
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=reply_text)
    )

# Handle image messages
@handler.add(MessageEvent, message=ImageMessage)
def handle_image(event):
    # Get image content from the LINE message
    message_content = line_bot_api.get_message_content(event.message.id)
    image_bytes = b""
    
    for chunk in message_content.iter_content():
        image_bytes += chunk
    
    # Convert image bytes to base64
    image_base64 = base64.b64encode(image_bytes).decode('utf-8')
    
    # Send the image to Clarifai for recognition
    try:
        clarifai_result = recognize_image_with_clarifai(image_base64)
        
        # Extract recognized concepts
        concepts = clarifai_result['data']['concepts']
        recognized_items = ', '.join([concept['name'] for concept in concepts])

        # Create a response message for the user
        reply_text = f"我在圖片中辨識出了以下物體: {recognized_items}"
    
    except Exception as e:
        reply_text = f"圖片辨識失敗: {str(e)}"
    
    # Send response back to the user
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=reply_text)
    )

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))  # Render will provide the PORT env variable
    app.run(host="0.0.0.0", port=port)
