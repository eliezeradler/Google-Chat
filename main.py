import os
import json
import io
import requests
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

SCOPES = [
    'https://www.googleapis.com/auth/chat.messages',
    'https://www.googleapis.com/auth/chat.spaces.readonly'
]
STATE_FILE = 'sync_state.txt'

def authenticate_google_chat():
    token_info = json.loads(os.environ['GCP_TOKEN'])
    creds = Credentials.from_authorized_user_info(token_info, SCOPES)
    return build('chat', 'v1', credentials=creds)

def download_attachment(attachment, creds):
    download_uri = attachment.get('attachmentDataRef', {}).get('downloadUri')
    if not download_uri:
        return None, None
    headers = {'Authorization': f'Bearer {creds.token}'}
    response = requests.get(download_uri, headers=headers)
    if response.status_code == 200:
        return io.BytesIO(response.content), attachment.get('contentType', 'application/octet-stream')
    return None, None

def get_all_messages(service, space_name):
    messages = []
    page_token = None
    try:
        while True:
            results = service.spaces().messages().list(
                parent=space_name, 
                pageSize=1000,
                pageToken=page_token
            ).execute()
            
            if 'messages' in results:
                messages.extend(results['messages'])
            
            page_token = results.get('nextPageToken')
            if not page_token:
                break
        return messages
    except Exception as e:
        print(f"שגיאה במשיכת הודעות: {e}")
        return []

def sync_new_messages(service, source_space, target_space):
    messages = get_all_messages(service, source_space)
    if not messages:
        print("לא נמצאו הודעות במרחב המקור.")
        return

    # קריאת מזהה ההודעה האחרונה מקובץ הזיכרון
    last_id = None
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, 'r', encoding='utf-8') as f:
            last_id = f.read().strip()

    # אם זה הרצה ראשונה ואין קובץ זיכרון, רק שומרים את ההודעה האחרונה ויוצאים
    if not last_id:
        latest_msg_id = messages[-1]['name']
        with open(STATE_FILE, 'w', encoding='utf-8') as f:
            f.write(latest_msg_id)
        print("ריצת אתחול: נשמר המזהה האחרון בזיכרון. ההעתקה תתחיל בפועל מהריצה הבאה.")
        return

    # איתור מאיפה להתחיל להעתיק
    index = -1
    for i, msg in enumerate(messages):
        if msg['name'] == last_id:
            index = i
            break

    new_messages = []
    if index != -1:
        new_messages = messages[index + 1:]
    else:
        # אם ההודעה האחרונה לא נמצאה (אולי נמחקה), ניקח את ה-50 האחרונות כברירת מחדל לגיבוי
        new_messages = messages[-50:]

    if not new_messages:
        print("אין הודעות חדשות להעתקה הפעם.")
        return

    print(f"נמצאו {len(new_messages)} הודעות חדשות. מתחיל העתקה...")

    for original_msg in new_messages:
        try:
            # 5. עיצוב שם השולח מודגש
            sender_name = original_msg.get('sender', {}).get('displayName', 'משתמש לא ידוע')
            original_text = original_msg.get('text', '')
            new_text = f"*{sender_name}:*\n{original_text}"
            
            attachments = original_msg.get('attachment', [])
            
            # אם אין קבצים מצורפים
            if not attachments:
                service.spaces().messages().create(
                    parent=target_space,
                    body={'text': new_text}
                ).execute()
                continue

            # 4. טיפול בריבוי קבצים מצורפים בהודעה אחת
            for i, attachment_info in enumerate(attachments):
                file_stream, mime_type = download_attachment(attachment_info, service.credentials)
                
                # הטקסט יישלח רק עם הקובץ הראשון. קבצים נוספים יישלחו כהודעות נלוות.
                msg_body = {'text': new_text} if i == 0 else {'text': f"*(קובץ נוסף מ-{sender_name})*"}
                
                if file_stream:
                    media_upload = MediaIoBaseUpload(file_stream, mimetype=mime_type, resumable=True)
                    service.spaces().messages().create(
                        parent=target_space,
                        body=msg_body,
                        media_body=media_upload
                    ).execute()
                else:
                    service.spaces().messages().create(
                        parent=target_space,
                        body=msg_body
                    ).execute()
                    
        except Exception as e:
            print(f"אירעה שגיאה בהעתקת הודעה {original_msg.get('name')}: {e}")

    # עדכון קובץ הזיכרון עם ה-ID של ההודעה האחרונה שהועתקה
    latest_msg_id = new_messages[-1]['name']
    with open(STATE_FILE, 'w', encoding='utf-8') as f:
        f.write(latest_msg_id)
    print("הסנכרון הסתיים בהצלחה וקובץ הזיכרון עודכן.")

if __name__ == '__main__':
    # הכנס כאן את המזהים שלך
    SOURCE_SPACE = 'spaces/AAQArWIpnWI'
    TARGET_SPACE = 'spaces/AAQAq5S0W9Q'
    
    chat_service = authenticate_google_chat()
    sync_new_messages(chat_service, SOURCE_SPACE, TARGET_SPACE)
