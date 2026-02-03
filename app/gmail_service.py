import base64
from datetime import datetime, timezone

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from google.auth.exceptions import RefreshError
from fastapi import HTTPException


async def get_daily_email(creds: Credentials):
    try:
        service = build('gmail', 'v1', credentials=creds)
        today = datetime.now(timezone.utc).strftime('%Y/%m/%d')
        query = f"after:{today}"
        results = service.users().messages().list(userId='me', q=query).execute()
    except RefreshError as e:
        raise HTTPException(
            status_code=401, 
            detail="Authentication token expired or invalid. Please re-authenticate."
        )
    messages = results.get('messages', [])

    email_texts = []
    for msg in messages:
        msg_detail = service.users().messages().get(userId='me', id=msg['id'], format='full').execute()
        headers = msg_detail.get('payload', {}).get('headers', [])
        subject = next((h['value'] for h in headers if h['name'].lower() == 'subject'), '')
        from_ = next((h['value'] for h in headers if h['name'].lower() == 'from'), '')
        to = next((h['value'] for h in headers if h['name'].lower() == 'to'), '')
        body = ''
        parts = msg_detail.get('payload', {}).get('parts', [])
        if parts:
            for part in parts:
                if part.get('mimeType') == 'text/plain':
                    body = part.get('body', {}).get('data', '')
                    break
        else:
            body = msg_detail.get('payload', {}).get('body', {}).get('data', '')
        try:
            body = base64.urlsafe_b64decode(body.encode('ASCII')).decode('utf-8') if body else ''
        except Exception:
            body = ''
        email_texts.append(f"Subject: {subject}\nFrom: {from_}\nTo: {to}\nBody: {body}")

    return email_texts

def get_service(creds: Credentials):
    return build('gmail', 'v1', credentials=creds)