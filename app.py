import sys
import configparser
import json, os
from datetime import datetime

# Azure Text Analytics
from azure.core.credentials import AzureKeyCredential
from azure.ai.textanalytics import TextAnalyticsClient

# Gemini API SDK
import google.generativeai as genai

from flask import Flask, request, jsonify,  abort
from linebot.v3 import (
    WebhookHandler
)
from linebot.v3.exceptions import (
    InvalidSignatureError
)
from linebot.v3.webhooks import (
    MessageEvent,
    TextMessageContent,
)
from linebot.v3.messaging import (
    Configuration,
    ApiClient,
    MessagingApi,
    ReplyMessageRequest,
    TextMessage,
    StickerMessage,
    ImageMessage,
    VideoMessage,
    LocationMessage
)

# 儲存聊天歷史的 JSON 檔案名稱
HISTORY_FILE = 'chat_history.json'

#Config Parser
config = configparser.ConfigParser()
config.read('config.ini')

#Config Azure Analytics
credential =AzureKeyCredential(config['AzureLanguage']['API_KEY'])
# Gemini API Settings
genai.configure(api_key=config["Gemini"]["API_KEY"])

role = """
妳是溫柔的心理醫師，會親切地回答客人的問題。
"""
# Use the model
from google.generativeai.types import HarmCategory, HarmBlockThreshold
model = genai.GenerativeModel(
    model_name="gemini-1.5-flash-latest",
    safety_settings={
        HarmCategory.HARM_CATEGORY_HARASSMENT:HarmBlockThreshold.BLOCK_NONE,
        HarmCategory.HARM_CATEGORY_HATE_SPEECH:HarmBlockThreshold.BLOCK_NONE,
        HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT:HarmBlockThreshold.BLOCK_NONE,
        HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT:HarmBlockThreshold.BLOCK_NONE,
    },
    generation_config={
        "temperature": 1,
        "top_p": 0.95,
        "top_k": 64,
        "max_output_tokens": 8192,
    },
    system_instruction=role,
)


app = Flask(__name__)

channel_access_token = config['Line']['CHANNEL_ACCESS_TOKEN']
channel_secret = config['Line']['CHANNEL_SECRET']
if channel_secret is None:
    print('Specify LINE_CHANNEL_SECRET as environment variable.')
    sys.exit(1)
if channel_access_token is None:
    print('Specify LINE_CHANNEL_ACCESS_TOKEN as environment variable.')
    sys.exit(1)

handler = WebhookHandler(channel_secret)

configuration = Configuration(
    access_token=channel_access_token
)

# ✅ RESTful API - 取得對話紀錄
@app.route('/history', methods=['GET'])
def get_history(): 
    history = load_history()
    return jsonify(history)

# ✅ RESTful API - 刪除對話紀錄
@app.route("/history", methods=['DELETE']) 
def delete_history():
    # 直接寫入空 dict，等於清空所有聊天紀錄
    save_history({})
    return jsonify({'status': 'success', 'message': 'All history deleted.'})

# 輔助函式：讀取聊天紀錄
def load_history():
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
            content = f.read().strip()
            if not content:
                return {}
            try:
                return json.loads(content)
            except json.JSONDecodeError:
                # 如果讀不到正確的 JSON，清空或備份後重置
                return {}
    else:
        return {}

# 輔助函式：寫入聊天紀錄
def save_history(data):
    with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

@app.route("/callback", methods=['POST'])
def callback():
    # get X-Line-Signature header value
    signature = request.headers['X-Line-Signature']
    # get request body as text
    body = request.get_data(as_text=True)
    app.logger.info("Request body: " + body)

    # parse webhook body
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

@handler.add(MessageEvent, message=TextMessageContent)
def message_text(event):
    with ApiClient(configuration) as api_client:
        event_message = event.message.text
        line_bot_api = MessagingApi(api_client)
        if event_message == 'sticker':
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[StickerMessage(package_id='1', sticker_id='2')]
                )
            )
        elif event_message == 'image':
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[ImageMessage(
                        original_content_url = "https://images.unsplash.com/photo-1503023345310-bd7c1de61c7d",
                        preview_image_url = "https://upload.wikimedia.org/wikipedia/commons/thumb/4/47/PNG_transparency_demonstration_1.png/240px-PNG_transparency_demonstration_1.png"
                    )]
                )
            )
        elif event_message == 'video':
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[VideoMessage(
                        original_content_url='https://download.blender.org/peach/bigbuckbunny_movies/BigBuckBunny_320x180.mp4',
                        preview_image_url='https://peach.blender.org/wp-content/uploads/title_anouncement.jpg?x11217'
                    )]
                )
            )
        elif event_message == 'location':
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[LocationMessage(
                       title='Mask Map',
                       address='花蓮',
                       latitude=23.601916,
                       longitude=121.5189989
                    )]
                )
            )
        else:
            gemini_result = gemini_llm_sdk(event.message.text)
            sentiment_result=azure_sentiment(event.message.text)
            line_bot_api.reply_message_with_http_info(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[
                        TextMessage(text=gemini_result + "\nAnalyze User's input: " + sentiment_result)
                    ]
                )
            )
              # 儲存聊天紀錄
            history = load_history()
            user_id = '大正妹'
            user_message = event.message.text
            
            if user_id not in history:
                history[user_id] = []
            history[user_id].append({
                'role': 'user',
                'message': user_message,
                'timestamp': datetime.now().isoformat()
            })
            save_history(history)

            bot_id = 'gemini_bot'
            if bot_id not in history:
                history[bot_id] = []     
            history[bot_id].append({
                'role': 'bot',
                'message': gemini_result,
                'timestamp': datetime.now().isoformat()
            })
            save_history(history)

def azure_sentiment(user_input):
    text_analytics_client = TextAnalyticsClient(
        endpoint=config['AzureLanguage']['END_POINT'], 
        credential=credential)
    documents = [user_input]
    response = text_analytics_client.analyze_sentiment(
        documents, 
        show_opinion_mining=True)
    print(response)
    docs = [doc for doc in response if not doc.is_error]
    for idx, doc in enumerate(docs):
        print(f"Document text : {documents[idx]}")
        print(f"Overall sentiment : {doc.sentiment}")
    return docs[0].sentiment

def gemini_llm_sdk(user_input):
    try:
        response = model.generate_content(user_input + "。請用繁體中文回答")
        print(f"Question: {user_input}")
        print(f"Answer: {response.text}")
        return response.text
    except Exception as e:
        print(e)
        return "皆麽奈夫人故障中。"
    
if __name__ == "__main__":
    app.run()