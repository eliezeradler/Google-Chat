import os
import json
import io
import requests
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload, MediaIoBaseDownload

SCOPES = [
    'https://www.googleapis.com/auth/chat.messages',
    'https://www.googleapis.com/auth/chat.spaces.readonly'
]
STATE_FILE = 'sync_data.json'

def authenticate_google_chat():
    token_info = json.loads(os.environ['GCP_TOKEN'])
    creds = Credentials.from_authorized_user_info(token_info, SCOPES)
    service = build('chat', 'v1', credentials=creds)
    return service, creds

def download_attachment(attachment, service, creds):
    attachment_ref = attachment.get('attachmentDataRef', {})
    download_uri = attachment_ref.get('downloadUri')
    resource_name = attachment_ref.get('resourceName')
    
    headers = {'Authorization': f'Bearer {creds.token}'}
    
    # מעקף: הורדה ישירה דרך ה-API (Media endpoint) ללא תלות בספריית פייתון
    if resource_name:
        # אנחנו מרכיבים את כתובת ה-API להורדת מדיה, וקובעים בכוח את alt=media
        media_url = f"https://chat.googleapis.com/v1/media/{resource_name}?alt=media"
        try:
            response = requests.get(media_url, headers=headers)
            if response.status_code == 200:
                print(f" > מדיה ירדה בהצלחה דרך API ישיר ({resource_name})")
                return io.BytesIO(response.content), attachment.get('contentType', 'application/octet-stream')
            else:
                print(f" > שגיאה בהורדת מדיה דרך API. סטטוס: {response.status_code}")
        except Exception as e:
            print(f" > שגיאת תקשורת בהורדה דרך API ישיר: {e}")

    # גיבוי: הורדה דרך קישור ישיר 
    elif download_uri:
        response = requests.get(download_uri, headers=headers)
        if response.status_code == 200:
            return io.BytesIO(response.content), attachment.get('contentType', 'application/octet-stream')
            
    print(" > שגיאה: לא ניתן היה להוריד את הקובץ המצורף (לא דרך API ולא דרך קישור).")
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
    if len(state['threads']) > 200:
        keys_to_keep = list(state['threads'].keys())[-200:]
        state['threads'] = {k: state['threads'][k] for k in keys_to_keep}
        
    with open(STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def sync_new_messages(service, creds, source_space, target_space):
    messages = get_all_messages(service, source_space)
    if not messages:
        print("לא נמצאו הודעות במרחב המקור.")
        return

    state = load_state()
    last_id = state.get("last_msg_id")

    if not last_id:
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
        new_messages = messages[-50:] 

    if not new_messages:
        print("אין הודעות חדשות להעתקה הפעם.")
        return

    print(f"נמצאו {len(new_messages)} הודעות חדשות. מתחיל העתקה...")

    for original_msg in new_messages:
        try:
            original_msg_id = original_msg.get('name', '')
            original_thread_id = original_msg.get('thread', {}).get('name', '')
            
            is_parent_message = False
            if original_msg_id and original_thread_id:
                msg_id_part = original_msg_id.split('/')[-1]
                thread_id_part = original_thread_id.split('/')[-1]
                is_parent_message = (msg_id_part == thread_id_part) or (msg_id_part == f"{thread_id_part}.{thread_id_part}")

            sender_info = original_msg.get('sender', {})
            sender_name = sender_info.get('displayName')
            if not sender_name:
                sender_name = sender_info.get('email', 'משתמש לא ידוע')

            original_text = original_msg.get('text', '')
            attachments = original_msg.get('attachment', [])
            
            if not original_text and not attachments:
                state["last_msg_id"] = original_msg_id
                continue

            new_text = f"*{sender_name}:*\n{original_text}" if original_text else f"*{sender_name}:*"
            msg_body = {'text': new_text}
            
            if not is_parent_message:
                if original_thread_id in state['threads']:
                    msg_body['thread'] = {'name': state['threads'][original_thread_id]}
                else:
                    print(f"דילוג: ההודעה {original_msg_id} היא תגובה לשרשור לא מוכר.")
                    state["last_msg_id"] = original_msg_id
                    continue 

            created_message = None

            if not attachments:
                api_kwargs = {'parent': target_space, 'body': msg_body}
                if 'thread' in msg_body:
                    api_kwargs['messageReplyOption'] = 'REPLY_MESSAGE_FALLBACK_TO_NEW_THREAD'
                
                created_message = service.spaces().messages().create(**api_kwargs).execute()
            else:
                for i, attachment_info in enumerate(attachments):
                    # הדפסת הנתונים למקרה של תקלה
                    print(f" > נתוני קובץ גולמיים: {json.dumps(attachment_info, ensure_ascii=False)}")
                    
                    # העברת ה-service כדי לאפשר הורדה של תמונות
                    file_stream, mime_type = download_attachment(attachment_info, service, creds)
                    
                    current_body = msg_body.copy() if i == 0 else {'text': f"*(קובץ נוסף מ-{sender_name})*"}
                    if 'thread' in msg_body:
                        current_body['thread'] = msg_body['thread']
                    
                    drive_id = attachment_info.get('driveDataRef', {}).get('driveFileId')
                    if drive_id:
                        drive_link = f"\n*🔗 מצורף קובץ Drive:* https://drive.google.com/file/d/{drive_id}/view"
                        current_body['text'] = current_body.get('text', '') + drive_link
                    
                    api_kwargs = {'parent': target_space, 'body': current_body}
                    if 'thread' in current_body:
                        api_kwargs['messageReplyOption'] = 'REPLY_MESSAGE_FALLBACK_TO_NEW_THREAD'
                    
                    if file_stream:
                        media_upload = MediaIoBaseUpload(file_stream, mimetype=mime_type, resumable=True)
                        api_kwargs['media_body'] = media_upload
                        msg_res = service.spaces().messages().create(**api_kwargs).execute()
                    else:
                        if not drive_id:
                            current_body['text'] += "\n*[מערכת: צורף קובץ או תמונה שלא ניתן היה להעתיק. בדוק את מרחב המקור.]*"
                        msg_res = service.spaces().messages().create(**api_kwargs).execute()
                        
                    if i == 0:
                        created_message = msg_res
                            
            if created_message and is_parent_message and original_thread_id:
                new_thread_id = created_message.get('thread', {}).get('name')
                if new_thread_id:
                    state['threads'][original_thread_id] = new_thread_id
            
            state["last_msg_id"] = original_msg_id
                    
        except Exception as e:
            print(f"אירעה שגיאה בהעתקת הודעה {original_msg.get('name')}: {e}")
            break

    save_state(state)
    print("הסנכרון הסתיים וקובץ הזיכרון (JSON) עודכן.")

if __name__ == '__main__':
    # ודא שהמזהים כאן נכונים עבור מרחב המקור ומרחב היעד שלך
    SOURCE_SPACE = 'spaces/AAQASiObNm8'
    TARGET_SPACE = 'spaces/AAQAq5S0W9Q'
    
    chat_service, creds = authenticate_google_chat()
    sync_new_messages(chat_service, creds, SOURCE_SPACE, TARGET_SPACE)
