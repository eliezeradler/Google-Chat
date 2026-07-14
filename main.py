def get_latest_message(service, space_name):
    """מושך את ההודעה האחרונה ביותר ממרחב המקור תוך מעבר על כל דפי ההיסטוריה"""
    try:
        # בדיקת חיבור: נדפיס את שם המרחב כדי לוודא שהטוקן שייך למשתמש הנכון
        print(f"בודק חיבור למרחב...")
        space_info = service.spaces().get(name=space_name).execute()
        print(f"מחובר בהצלחה למרחב: {space_info.get('displayName', 'ללא שם (Direct Message)')}")
        
        messages = []
        page_token = None
        page_count = 0
        
        # לולאה שמושכת את כל ההודעות עד שמגיעים לעמוד האחרון
        while True:
            page_count += 1
            results = service.spaces().messages().list(
                parent=space_name, 
                pageSize=1000, # מושך 1000 הודעות במכה כדי לייעל את התהליך
                pageToken=page_token
            ).execute()
            
            if 'messages' in results:
                messages.extend(results['messages'])
            
            # בודק אם יש עוד עמודים למשוך
            page_token = results.get('nextPageToken')
            if not page_token:
                break
                
        if not messages:
            print("לא נמצאו הודעות כלל במרחב (גם לאחר מעבר על כל דפי ההיסטוריה).")
            return None
            
        print(f"נסרקו {page_count} עמודים. סך הכל נמצאו {len(messages)} הודעות פעילות.")
        
        # ניקח את ההודעה האחרונה מהרשימה המלאה
        latest_message = messages[-1]
        print(f"ההודעה המעודכנת ביותר שנמצאה: מזהה {latest_message.get('name')}")
        return latest_message
        
    except Exception as e:
        print(f"שגיאה במשיכת היסטוריית ההודעות: {e}")
        return None
