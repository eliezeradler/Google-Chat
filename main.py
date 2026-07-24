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
STATE_FILE = 'sync_data.json'

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

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {"last_msg_id": None, "threads": {}}

def save_state(state):
    # שומר רק את ה-200 שרשורים האחרונים כדי שהקובץ לא יגדל לנצח
    if len(state['threads']) > 200:
        keys_to_keep = list(state['threads'].keys())[-200:]
        state['threads'] = {k: state['threads'][k] for k in keys_to_keep}
        
    with open(STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def sync_new_messages(service, source_space, target_space):
    messages = get_all_messages(service, source_space)
    if not messages:
        print("לא נמצאו הודעות במרחב המקור.")
        return

    state = load_state()
    last_id = state.get("last_msg_id")

    if not last_id:
        # ריצת אתחול
        state["last_msg_id"] = messages[-1]['name']
        save_state(state)
        print("ריצת אתחול: נשמר המזהה האחרון. ההעתקה תתחיל בפועל מהריצה הבאה.")
        return

    index = -1
    for i, msg in enumerate(messages):
        if msg['name'] == last_id:
            index = i
            break

    new_messages = []
    if index != -1:
        new_messages = messages[index + 1:]
    else:
        new_messages = messages[-50:] # גיבוי אם ההודעה נמחקה

    if not new_messages:
        print("אין הודעות חדשות להעתקה הפעם.")
        return

    print(f"נמצאו {len(new_messages)} הודעות חדשות. מתחיל העתקה...")

    for original_msg in new_messages:
        try:
            original_msg_id = original_msg.get('name', '')
            original_thread_id = original_msg.get('thread', {}).get('name', '')
            
            # בדיקה: האם זו הודעה ראשית או תגובה?
            is_parent_message = False
            if original_msg_id and original_thread_id:
                # חיתוך המזהה וניקוי סיומות כדי להשוות נכון (מתעלם ממה שאחרי הנקודה)
                msg_id_part = original_msg_id.split('/')[-1].split('.')[0]
                thread_id_part = original_thread_id.split('/')[-1].split('.')[0]
                
                # אם המזהה הנקי של ההודעה זהה למזהה השרשור - זו ההודעה הראשית
                is_parent_message = (msg_id_part == thread_id_part)

            sender_name = original_msg.get('sender', {}).get('displayName', 'משתמש לא ידוע')
            original_text = original_msg.get('text', '')
            new_text = f"*{sender_name}:*\n{original_text}"
            
            msg_body = {'text': new_text}
            
            if not is_parent_message:
                # זוהי תגובה. נבדוק אם השרשור קיים בזיכרון
                if original_thread_id in state['threads']:
                    msg_body['thread'] = {'name': state['threads'][original_thread_id]}
                else:
                    print(f"דילוג: ההודעה {original_msg_id} היא תגובה לשרשור לא מוכר. מדלג כדי לא להעלות לראשי.")
                    continue # עובר להודעה הבאה בלולאה מבלי להעתיק!

            attachments = original_msg.get('attachment', [])
            created_message = None

            if not attachments:
                created_message = service.spaces().messages().create(
                    parent=target_space,
                    body=msg_body
                ).execute()
            else:
                for i, attachment_info in enumerate(attachments):
                    file_stream, mime_type = download_attachment(attachment_info, service.credentials)
                    
                    # הטקסט המלא נשלח רק בקובץ הראשון
                    current_body = msg_body.copy() if i == 0 else {'text': f"*(קובץ נוסף מ-{sender_name})*"}
                    if 'thread' in msg_body:
                        current_body['thread'] = msg_body['thread']
                    
                    if file_stream:
                        media_upload = MediaIoBaseUpload(file_stream, mimetype=mime_type, resumable=True)
                        msg_res = service.spaces().messages().create(
                            parent=target_space,
                            body=current_body,
                            media_body=media_upload
                        ).execute()
                        if i == 0:
                            created_message = msg_res
                    else:
                        msg_res = service.spaces().messages().create(
                            parent=target_space,
                            body=current_body
                        ).execute()
                        if i == 0:
                            created_message = msg_res
                            
            # עדכון מילון השרשורים (רק אם זו הודעה ראשית)
            if created_message and is_parent_message and original_thread_id:
                new_thread_id = created_message.get('thread', {}).get('name')
                if new_thread_id:
                    state['threads'][original_thread_id] = new_thread_id
                    
        except Exception as e:
            print(f"אירעה שגיאה בהעתקת הודעה {original_msg.get('name')}: {e}")

    state["last_msg_id"] = new_messages[-1]['name']
    save_state(state)
    print("הסנכרון הסתיים בהצלחה וקובץ הזיכרון (JSON) עודכן.")

if __name__ == '__main__':
    SOURCE_SPACE = 'spaces/AAQArWIpnWI'
    TARGET_SPACE = 'spaces/AAQAq5S0W9Q'
    
    chat_service = authenticate_google_chat()
    sync_new_messages(chat_service, SOURCE_SPACE, TARGET_SPACE)
