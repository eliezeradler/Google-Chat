import os
import json
import io
import time
import requests
import random
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload, MediaIoBaseDownload

SCOPES = [
    'https://www.googleapis.com/auth/chat.messages',
    'https://www.googleapis.com/auth/chat.spaces.readonly',
    'https://www.googleapis.com/auth/chat.memberships.readonly'
]

USERS_FILE = 'users.json'

# --- קיצוב קצב + Retry גלובלי לכל כתיבה ל-Chat API ---
MIN_WRITE_INTERVAL = 1.2
_last_write_ts = 0.0

def pace_write():
    global _last_write_ts
    now = time.time()
    wait_needed = MIN_WRITE_INTERVAL - (now - _last_write_ts)
    if wait_needed > 0:
        time.sleep(wait_needed)
    _last_write_ts = time.time()

def call_with_backoff(func, label="", max_attempts=7, base_delay=3, max_wait=60):
    last_exc = None
    for attempt in range(max_attempts):
        pace_write()
        try:
            return func()
        except Exception as e:
            last_exc = e
            msg = str(e)
            if ('429' in msg or '503' in msg) and attempt < max_attempts - 1:
                jitter = random.uniform(0, 1)
                wait_time = min(max_wait, base_delay * (2 ** attempt)) + jitter
                print(f" > עומס/זמינות בכתיבה ({label}). ממתין {wait_time:.2f} שניות ומנסה שוב (ניסיון {attempt + 1}/{max_attempts})...")
                time.sleep(wait_time)
            else:
                break
    raise last_exc

def load_users_dict():
    if os.path.exists(USERS_FILE):
        try:
            with open(USERS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"שגיאה בטעינת קובץ המשתמשים {USERS_FILE}: {e}")
    return {}

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
    
    if resource_name:
        media_url = f"https://chat.googleapis.com/v1/media/{resource_name}?alt=media"
        for attempt in range(3):
            try:
                response = requests.get(media_url, headers=headers, timeout=30)
                if response.status_code == 200:
                    print(f" > מדיה ירדה בהצלחה דרך API ישיר ({resource_name})")
                    return io.BytesIO(response.content), attachment.get('contentType', 'application/octet-stream')
                elif response.status_code == 429:
                    print(f" > עומס קריאה (429) בהורדה. ממתין 10 שניות...")
                    time.sleep(10)
                else:
                    break
            except Exception as e:
                print(f" > שגיאת תקשורת בהורדה: {e}")
                time.sleep(5)

    elif download_uri:
        for attempt in range(3):
            try:
                response = requests.get(download_uri, headers=headers, timeout=30)
                if response.status_code == 200:
                    return io.BytesIO(response.content), attachment.get('contentType', 'application/octet-stream')
                elif response.status_code == 429:
                    print(f" > עומס קריאה (429) מקישור. ממתין 10 שניות...")
                    time.sleep(10)
                else:
                    break
            except Exception as e:
                print(f" > שגיאת תקשורת מקישור: {e}")
                time.sleep(5)
            
    print(" > שגיאה: לא ניתן היה להוריד את הקובץ המצורף.")
    return None, None

def get_all_messages(service, space_name):
    messages = []
    page_token = None
    try:
        while True:
            results = call_with_backoff(
                lambda: service.spaces().messages().list(
                    parent=space_name,
                    pageSize=1000,
                    pageToken=page_token
                ).execute(),
                label="קריאת הודעות"
            )
            
            if 'messages' in results:
                messages.extend(results['messages'])
            
            page_token = results.get('nextPageToken')
            if not page_token:
                break
                
        return messages
    except Exception as e:
        print(f"שגיאה במשיכת הודעות (לאחר כל הניסיונות): {e}")
        return None

def get_state_file(target_space):
    if target_space == 'spaces/AAQAq5S0W9Q':
        return 'sync_data.json'
    target_id = target_space.split('/')[-1]
    return f'sync_data_{target_id}.json'

def load_state(target_space):
    state_file = get_state_file(target_space)
    if os.path.exists(state_file):
        with open(state_file, 'r', encoding='utf-8') as f:
            state = json.load(f)
            if "processed_ids" not in state: 
                state["processed_ids"] = []
            return state
    return {"last_msg_id": None, "threads": {}, "processed_ids": []}

def save_state(state, target_space):
    state_file = get_state_file(target_space)
    if len(state.get('threads', {})) > 200:
        keys_to_keep = list(state['threads'].keys())[-200:]
        state['threads'] = {k: state['threads'][k] for k in keys_to_keep}
        
    state['processed_ids'] = state.get('processed_ids', [])[-500:]
        
    with open(state_file, 'w', encoding='utf-8') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def resolve_sender_display(original_msg, source_space, service, known_users, dynamic_known_users):
    """
    מאתר את שם המשתמש מתוך קובץ המילון החיצוני (users.json) או ה-API.
    אם קיים מזהה משתמש, מחזיר תגית לחיצה <users/ID> בלבד ללא כפילות.
    אם המזהה לא קיים במילון, מחזיר את המזהה עצמו במקום 'משתמש לא ידוע'.
    """
    sender_info = original_msg.get('sender', {})
    raw_name = sender_info.get('name', '')
    clean_id = raw_name.replace('users/', '') if raw_name else ''

    # 1. בדיקה במילון החיצוני (תומך גם במזהה נקי וגם בקידומת users/)
    sender_name = known_users.get(clean_id) or known_users.get(raw_name)

    # 2. בדיקה במילון הדינמי של הריצה הנוכחית
    if not sender_name and clean_id in dynamic_known_users:
        sender_name = dynamic_known_users[clean_id]

    # 3. בדיקה בשדות ההודעה הישירים
    if not sender_name:
        sender_name = sender_info.get('displayName') or sender_info.get('email')

    # 4. ניסיון שליפה מה-API של המרחב
    if not sender_name and clean_id:
        try:
            member_resource = f"{source_space}/members/{clean_id}"
            member_info = service.spaces().members().get(name=member_resource).execute()
            user_data = member_info.get('member', {})
            sender_name = user_data.get('displayName') or user_data.get('email')
            if sender_name:
                dynamic_known_users[clean_id] = sender_name
        except Exception:
            pass

    # 5. אם עדיין אין שם במילון או ב-API -> הצגת המזהה עצמו במקום 'משתמש לא ידוע'
    if not sender_name:
        sender_name = clean_id if clean_id else (raw_name if raw_name else "ללא_מזהה")

    # אפשרות 1: הצגת התיוג הלחיץ בלבד (ללא כפילות של השם לפניו)
    if clean_id:
        return f"<users/{clean_id}>", sender_name
    return f"*{sender_name}*", sender_name

def sync_new_messages(service, creds, source_space, target_space, known_users):
    messages = get_all_messages(service, source_space)
    
    if messages is None:
        print(f"דילוג על {source_space} בריצה הזו עקב שגיאת תקשורת (ה-API לא הגיב כראוי). ינסה שוב בריצה הבאה.")
        return
    
    if not messages:
        print(f"לא נמצאו הודעות במרחב המקור {source_space}.")
        return

    state = load_state(target_space)
    last_id = state.get("last_msg_id")

    if not last_id:
        state["last_msg_id"] = messages[-1]['name']
        save_state(state, target_space)
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

    dynamic_known_users = {}

    for original_msg in new_messages:
        try:
            original_msg_id = original_msg.get('name', '')
            original_text = original_msg.get('text', '')
            
            if original_msg_id in state.get('processed_ids', []):
                print(f"דילוג: הודעה {original_msg_id} כבר הועתקה בעבר.")
                continue

            original_thread_id = original_msg.get('thread', {}).get('name', '')
            
            is_parent_message = False
            if original_msg_id and original_thread_id:
                msg_id_part = original_msg_id.split('/')[-1]
                thread_id_part = original_thread_id.split('/')[-1]
                is_parent_message = (msg_id_part == thread_id_part) or (msg_id_part == f"{thread_id_part}.{thread_id_part}")

            sender_header, sender_plain_name = resolve_sender_display(
                original_msg, source_space, service, known_users, dynamic_known_users
            )

            attachments = original_msg.get('attachment', [])
            
            if not original_text and not attachments:
                state["last_msg_id"] = original_msg_id
                if original_msg_id not in state['processed_ids']:
                    state['processed_ids'].append(original_msg_id)
                save_state(state, target_space) 
                continue

            new_text = f"{sender_header}:\n{original_text}" if original_text else f"{sender_header}:"
            msg_body = {'text': new_text}
            
            if not is_parent_message:
                if original_thread_id in state['threads']:
                    msg_body['thread'] = {'name': state['threads'][original_thread_id]}
                else:
                    print(f"דילוג: ההודעה {original_msg_id} היא תגובה לשרשור לא מוכר.")
                    state["last_msg_id"] = original_msg_id
                    if original_msg_id not in state['processed_ids']:
                        state['processed_ids'].append(original_msg_id)
                    save_state(state, target_space) 
                    continue 

            created_message = None

            if not attachments:
                api_kwargs = {'parent': target_space, 'body': msg_body}
                if 'thread' in msg_body:
                    api_kwargs['messageReplyOption'] = 'REPLY_MESSAGE_FALLBACK_TO_NEW_THREAD'
                
                created_message = call_with_backoff(
                    lambda: service.spaces().messages().create(**api_kwargs).execute(),
                    label="יצירת הודעת טקסט"
                )
                print(" > הודעת טקסט הועתקה בהצלחה.")
            else:
                for i, attachment_info in enumerate(attachments):
                    file_stream, mime_type = download_attachment(attachment_info, service, creds)
                    
                    current_body = msg_body.copy() if i == 0 else {'text': f"*(קובץ נוסף מ-{sender_plain_name})*"}
                    if 'thread' in msg_body:
                        current_body['thread'] = msg_body['thread']
                    msg_res = None
                    
                    drive_id = attachment_info.get('driveDataRef', {}).get('driveFileId')
                    if drive_id:
                        drive_link = f"\n*🔗 מצורף קובץ Drive:* https://drive.google.com/file/d/{drive_id}/view"
                        current_body['text'] = current_body.get('text', '') + drive_link
                    
                    api_kwargs = {'parent': target_space, 'body': current_body}
                    if 'thread' in current_body:
                        api_kwargs['messageReplyOption'] = 'REPLY_MESSAGE_FALLBACK_TO_NEW_THREAD'
                    
                    if file_stream:
                        file_name = attachment_info.get('contentName', 'attachment_file')
                        upload_res = None
                        last_error_msg = "שגיאה לא ידועה"

                        def _do_upload():
                            file_stream.seek(0)
                            media_upload = MediaIoBaseUpload(file_stream, mimetype=mime_type, resumable=True)
                            return service.media().upload(
                                parent=target_space,
                                body={'filename': file_name},
                                media_body=media_upload
                            ).execute()

                        try:
                            upload_res = call_with_backoff(_do_upload, label=f"העלאת קובץ {file_name}")
                        except Exception as e:
                            last_error_msg = str(e)
                            upload_res = None
                            if '429' in last_error_msg or '503' in last_error_msg:
                                print(" > כל 7 הניסיונות נכשלו על רקע מכסה - השהיה נוספת של 20 שניות לפני שממשיכים, כדי לתת למכסה להתאפס.")
                                time.sleep(20)

                        if upload_res:
                            attachment_data_ref = upload_res.get('attachmentDataRef')
                            if attachment_data_ref:
                                current_body['attachment'] = [{'attachmentDataRef': attachment_data_ref}]
                            
                            try:
                                msg_res = call_with_backoff(
                                    lambda: service.spaces().messages().create(**api_kwargs).execute(),
                                    label="יצירת הודעה עם קובץ"
                                )
                                print(f" > קובץ ({file_name}) טופל בהצלחה.")
                            except Exception as e:
                                print(f" > שגיאה בשליחת ההודעה (לאחר כל הניסיונות): {e}")
                        else:
                            current_body['text'] += f"\n*[מערכת: קובץ ({file_name}) לא צורף. סיבה: {last_error_msg}]*"
                            try:
                                msg_res = call_with_backoff(
                                    lambda: service.spaces().messages().create(**api_kwargs).execute(),
                                    label="יצירת הודעת שגיאה"
                                )
                            except Exception as e:
                                print(f" > שגיאה בשליחת הודעת השגיאה (לאחר כל הניסיונות): {e}")
                    else:
                        if not drive_id:
                            current_body['text'] += "\n*[מערכת: צורף קובץ או תמונה שלא ניתן היה להוריד ממרחב המקור]*"
                        try:
                            msg_res = call_with_backoff(
                                lambda: service.spaces().messages().create(**api_kwargs).execute(),
                                label="יצירת הודעת שגיאת הורדה"
                            )
                        except Exception as e:
                            print(f" > שגיאה בשליחת הודעת שגיאת הורדה (לאחר כל הניסיונות): {e}")
                        
                    if i == 0:
                        created_message = msg_res
                            
            if created_message and is_parent_message and original_thread_id:
                new_thread_id = created_message.get('thread', {}).get('name')
                if new_thread_id:
                    state['threads'][original_thread_id] = new_thread_id
            
            state["last_msg_id"] = original_msg_id
            if original_msg_id not in state['processed_ids']:
                state['processed_ids'].append(original_msg_id)
                
            save_state(state, target_space)
                    
        except Exception as e:
            print(f"אירעה שגיאה בהעתקת הודעה {original_msg.get('name')}: {e}")
            continue

    print(f"הסנכרון מ-{source_space} הסתיים בהצלחה.\n")

if __name__ == '__main__':
    SPACE_PAIRS = [
        ('spaces/AAQArWIpnWI', 'spaces/AAQAq5S0W9Q'),
        ('spaces/AAQAKJsiBR0', 'spaces/AAQA89OFw6A')
    ]
    
    known_users_dict = load_users_dict()
    print(f"נטענו {len(known_users_dict)} משתמשים מקובץ המילון החיצוני ({USERS_FILE}).")
    
    chat_service, creds = authenticate_google_chat()
    
    for source, target in SPACE_PAIRS:
        print(f"--- מתחיל סנכרון: {source} >>> {target} ---")
        sync_new_messages(chat_service, creds, source, target, known_users_dict)
