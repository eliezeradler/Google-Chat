import os
import json
import io
import requests
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

SCOPES = ['https://www.googleapis.com/auth/chat.messages']

def authenticate_google_chat():
    """התחברות באמצעות הטוקן שנשמר בסודות של גיטהאב"""
    token_info = json.loads(os.environ['GCP_TOKEN'])
    creds = Credentials.from_authorized_user_info(token_info, SCOPES)
    return build('chat', 'v1', credentials=creds)

def download_attachment(attachment, creds):
    """מוריד את הקובץ המצורף לזיכרון הזמני של השרת"""
    download_uri = attachment.get('attachmentDataRef', {}).get('downloadUri')
    if not download_uri:
        return None, None
        
    # משתמשים בטוקן שלנו כדי לקבל הרשאת הורדה לגוגל
    headers = {'Authorization': f'Bearer {creds.token}'}
    response = requests.get(download_uri, headers=headers)
    
    if response.status_code == 200:
        return io.BytesIO(response.content), attachment.get('contentType', 'application/octet-stream')
    else:
        print(f"שגיאה בהורדת הקובץ: {response.status_code}")
        return None, None

def copy_message(service, source_space, target_space, message_id):
    """מעתיק את ההודעה ואת הקובץ המצורף"""
    try:
        # 1. קריאת ההודעה המקורית
        message_name = f"{source_space}/messages/{message_id}"
        original_msg = service.spaces().messages().get(name=message_name).execute()
        
        new_msg_content = {'text': original_msg.get('text', '')}
        media_upload = None
        
        # 2. בדיקה אם יש קובץ מצורף והורדה שלו לזיכרון
        if 'attachment' in original_msg and len(original_msg['attachment']) > 0:
            attachment_info = original_msg['attachment'][0]
            file_stream, mime_type = download_attachment(attachment_info, service.credentials)
            
            if file_stream:
                # 3. הכנת הקובץ להעלאה ישירה למרחב החדש
                media_upload = MediaIoBaseUpload(
                    file_stream, 
                    mimetype=mime_type, 
                    resumable=True
                )

        # 4. יצירת ההודעה במרחב היעד
        if media_upload:
            service.spaces().messages().create(
                parent=target_space,
                body=new_msg_content,
                media_body=media_upload
            ).execute()
            print("ההודעה והקובץ המצורף הועתקו בהצלחה!")
        else:
            service.spaces().messages().create(
                parent=target_space,
                body=new_msg_content
            ).execute()
            print("הודעת הטקסט הועתקה בהצלחה (ללא קבצים)!")
            
    except Exception as e:
        print(f"אירעה שגיאה: {e}")

if __name__ == '__main__':
    # מזהי המרחבים שלך
    SOURCE_SPACE = 'spaces/AAQAGYJvZLw'
    TARGET_SPACE = 'spaces/AAQAq5S0W9Q'
    
    # חובה להזין כאן את מזהה ההודעה שברצונך להעתיק כעת
    MESSAGE_TO_COPY_ID = 'הכנס_כאן_את_מזהה_ההודעה' 
    
    print("מתחבר לשירות...")
    chat_service = authenticate_google_chat()
    
    print("מעתיק הודעה וקבצים...")
    copy_message(chat_service, SOURCE_SPACE, TARGET_SPACE, MESSAGE_TO_COPY_ID)
