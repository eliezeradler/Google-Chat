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
        
    headers = {'Authorization': f'Bearer {creds.token}'}
    response = requests.get(download_uri, headers=headers)
    
    if response.status_code == 200:
        return io.BytesIO(response.content), attachment.get('contentType', 'application/octet-stream')
    else:
        print(f"שגיאה בהורדת הקובץ: {response.status_code}")
        return None, None

def get_latest_message(service, space_name):
    """מושך את ההודעה האחרונה ביותר ממרחב המקור"""
    try:
        # בקשת רשימת ההודעות האחרונות מהמרחב
        results = service.spaces().messages().list(
            parent=space_name, 
            pageSize=50
        ).execute()
        
        messages = results.get('messages', [])
        if not messages:
            print("לא נמצאו הודעות במרחב המקור.")
            return None
            
        # הרשימה חוזרת בסדר כרונולוגי, לכן ניקח את ההודעה האחרונה במערך
        latest_message = messages[-1]
        print(f"נמצאה הודעה להעתקה. מזהה: {latest_message.get('name')}")
        return latest_message
        
    except Exception as e:
        print(f"שגיאה במשיכת היסטוריית ההודעות: {e}")
        return None

def copy_message(service, source_space, target_space):
    """מאתר את ההודעה האחרונה ומעתיק אותה כולל קבצים"""
    original_msg = get_latest_message(service, source_space)
    
    if not original_msg:
        print("פעולת ההעתקה בוטלה מכיוון שלא נמצאה הודעה.")
        return

    try:
        new_msg_content = {'text': original_msg.get('text', '')}
        media_upload = None
        
        # בדיקה אם יש קובץ מצורף והורדה שלו
        if 'attachment' in original_msg and len(original_msg['attachment']) > 0:
            attachment_info = original_msg['attachment'][0]
            print("מוריד קובץ מצורף...")
            file_stream, mime_type = download_attachment(attachment_info, service.credentials)
            
            if file_stream:
                media_upload = MediaIoBaseUpload(
                    file_stream, 
                    mimetype=mime_type, 
                    resumable=True
                )

        # יצירת ההודעה במרחב היעד
        if media_upload:
            service.spaces().messages().create(
                parent=target_space,
                body=new_msg_content,
                media_body=media_upload
            ).execute()
            print("ההודעה והקובץ המצורף הועתקו בהצלחה למרחב היעד!")
        else:
            service.spaces().messages().create(
                parent=target_space,
                body=new_msg_content
            ).execute()
            print("הודעת הטקסט הועתקה בהצלחה (ללא קבצים מצורפים)!")
            
    except Exception as e:
        print(f"אירעה שגיאה בעת יצירת ההודעה החדשה: {e}")

if __name__ == '__main__':
    # מזהי המרחבים שהגדרנו
    SOURCE_SPACE = 'spaces/AAQAGYJvZLw'
    TARGET_SPACE = 'spaces/AAQAq5S0W9Q'
    
    print("מתחבר לשירות...")
    chat_service = authenticate_google_chat()
    
    print("מתחיל בתהליך איתור והעתקת ההודעה...")
    copy_message(chat_service, SOURCE_SPACE, TARGET_SPACE)
