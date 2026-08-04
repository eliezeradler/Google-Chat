import os
import json
import io
import time
import requests
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload, MediaIoBaseDownload

SCOPES = [
    'https://www.googleapis.com/auth/chat.messages',
    'https://www.googleapis.com/auth/chat.spaces.readonly',
    'https://www.googleapis.com/auth/chat.memberships.readonly'
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
    
    if resource_name:
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
            state = json.load(f)
            if "processed_ids" not in state: 
                state["processed_ids"] = []
            return state
    return {"last_msg_id": None, "threads": {}, "processed_ids": []}

def save_state(state):
    if len(state.get('threads', {})) > 200:
        keys_to_keep = list(state['threads'].keys())[-200:]
        state['threads'] = {k: state['threads'][k] for k in keys_to_keep}
        
    state['processed_ids'] = state.get('processed_ids', [])[-500:]
        
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

            sender_info = original_msg.get('sender', {})
            sender_name = sender_info.get('displayName')
            if not sender_name:
                sender_name = sender_info.get('email')
            
            if not sender_name:
                raw_name = sender_info.get('name', '')
                if raw_name:
                    known_users = {
                        "users/107137395716236885442": "פפה",
                        "users/106503506710148158594": "דני לוי",
                        "users/107877662890602550681": "אבי רוז",
                        "users/107234283163890610021": "Yael",
                        "users/117513968213821700596": "חיים ה. ו.",
                        "users/100563310580630823467": "בוטית שלי",
                        "users/108047377691216153736": "TamTam",
                        "users/114085465098324901258": "מלי",
                        "users/115105722837621769589": "אורי דווידי",
                        "users/107569218113942296678": "יוסף פטרובר",
                        "users/101534845067525560683": "Shai",
                        "users/101850995062930362213": "family 2025",
                        "users/117693190766287637519": "ניהול חדש",
                        "users/117147849218349801765": "אברהם פרידמן",
                        "users/114525315288128139376": "levkivker",
                        "users/100961944946973009260": "Netanel",
                        "users/113248425146167624902": "s.levkivker",
                        "users/107235267519492805137": "Ben Ziyon g",
                        "users/110801357268126058232": "שניאור א.",
                        "users/103092947269637100183": "אלעזר",
                        "users/115022370288768837848": "שלמה וי",
                        "users/108727139455424835546": "Haim Furman",
                        "users/112628871561495302517": "Shloimy Getter"
                    }
                    
                    if raw_name in known_users:
                        sender_name = known_users[raw_name]
                    else:
                        try:
                            user_id = raw_name.split('/')[-1]
                            member_resource = f"{source_space}/members/{user_id}"
                            member_info = service.spaces().members().get(name=member_resource).execute()
                            user_data = member_info.get('member', {})
                            sender_name = user_data.get('displayName')
                            if not sender_name:
                                sender_name = user_data.get('email')
                            if not sender_name:
                                sender_name = f"מזהה: {user_id}"
                        except Exception as e:
                            sender_name = f"מזהה: {raw_name.split('/')[-1]}"
            else:
                sender_name = 'משתמש לא ידוע'

            attachments = original_msg.get('attachment', [])
            
            if not original_text and not attachments:
                state["last_msg_id"] = original_msg_id
                if original_msg_id not in state['processed_ids']:
                    state['processed_ids'].append(original_msg_id)
                save_state(state) 
                continue

            new_text = f"*{sender_name}:*\n{original_text}" if original_text else f"*{sender_name}:*"
            msg_body = {'text': new_text}
            
            if not is_parent_message:
                if original_thread_id in state['threads']:
                    msg_body['thread'] = {'name': state['threads'][original_thread_id]}
                else:
                    print(f"דילוג: ההודעה {original_msg_id} היא תגובה לשרשור לא מוכר.")
                    state["last_msg_id"] = original_msg_id
                    if original_msg_id not in state['processed_ids']:
                        state['processed_ids'].append(original_msg_id)
                    save_state(state) 
                    continue 

            created_message = None

            if not attachments:
                api_kwargs = {'parent': target_space, 'body': msg_body}
                if 'thread' in msg_body:
                    api_kwargs['messageReplyOption'] = 'REPLY_MESSAGE_FALLBACK_TO_NEW_THREAD'
                
                created_message = service.spaces().messages().create(**api_kwargs).execute()
                print(" > הודעת טקסט הועתקה בהצלחה.")
            else:
                for i, attachment_info in enumerate(attachments):
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
                        file_name = attachment_info.get('contentName', 'attachment_file')
                        upload_res = None
                        
                        for attempt in range(3):
                            try:
                                file_stream.seek(0)
                                media_upload = MediaIoBaseUpload(file_stream, mimetype=mime_type, resumable=True)
                                
                                upload_res = service.media().upload(
                                    parent=target_space,
                                    body={'filename': file_name},
                                    media_body=media_upload
                                ).execute()
                                break
                                
                            except Exception as e:
                                if '429' in str(e) and attempt < 2:
                                    wait_time = 15
                                    print(f" > עומס רגעי (429) בהעלאת {file_name}. ממתין {wait_time} שניות ומנסה שוב (ניסיון {attempt + 2}/3)...")
                                    time.sleep(wait_time)
                                else:
                                    print(f" > שגיאה סופית בהעלאת מדיה למרחב היעד: {e}")
                                    break

                        if upload_res:
                            attachment_data_ref = upload_res.get('attachmentDataRef')
                            if attachment_data_ref:
                                current_body['attachment'] = [{'attachmentDataRef': attachment_data_ref}]
                            
                            try:
                                msg_res = service.spaces().messages().create(**api_kwargs).execute()
                                print(f" > קובץ ({file_name}) טופל בהצלחה. ממתין 3 שניות לשחרור עומס...")
                                time.sleep(3)
                            except Exception as e:
                                print(f" > שגיאה בשליחת ההודעה לאחר העלאת המדיה: {e}")
                        else:
                            current_body['text'] += f"\n*[מערכת: התרחשה שגיאה במהלך צירוף הקובץ ({file_name}) להודעה]*"
                            msg_res = service.spaces().messages().create(**api_kwargs).execute()
                    else:
                        if not drive_id:
                            current_body['text'] += "\n*[מערכת: צורף קובץ או תמונה שלא ניתן היה להוריד ממרחב המקור]*"
                        msg_res = service.spaces().messages().create(**api_kwargs).execute()
                        
                    if i == 0:
                        created_message = msg_res
                            
            if created_message and is_parent_message and original_thread_id:
                new_thread_id = created_message.get('thread', {}).get('name')
                if new_thread_id:
                    state['threads'][original_thread_id] = new_thread_id
            
            state["last_msg_id"] = original_msg_id
            if original_msg_id not in state['processed_ids']:
                state['processed_ids'].append(original_msg_id)
                
            save_state(state)
                    
        except Exception as e:
            print(f"אירעה שגיאה בהעתקת הודעה {original_msg.get('name')}: {e}")
            continue

    print("הסנכרון הסתיים בהצלחה.")

if __name__ == '__main__':
    SOURCE_SPACE = 'spaces/AAQArWIpnWI'
    TARGET_SPACE = 'spaces/AAQAq5S0W9Q'
    
    chat_service, creds = authenticate_google_chat()
    sync_new_messages(chat_service, creds, SOURCE_SPACE, TARGET_SPACE)
