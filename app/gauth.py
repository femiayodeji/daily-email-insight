from fastapi import Request, HTTPException
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from google.auth.exceptions import RefreshError

from app.config import GOOGLE_CLIENT_SECRETS, GOOGLE_REDIRECT_URI, SCOPES

def get_flow():
    return Flow.from_client_secrets_file(
        GOOGLE_CLIENT_SECRETS,
        scopes=SCOPES,
        redirect_uri=GOOGLE_REDIRECT_URI
    )

def get_credentials(request: Request):
    creds_data = request.session.get('credentials')
    if not creds_data:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    creds = Credentials(**creds_data)
    
    if creds.expired and creds.refresh_token:
        try:
            from google.auth.transport.requests import Request as GoogleRequest
            creds.refresh(GoogleRequest())
            request.session['credentials'] = {
                'token': creds.token,
                'refresh_token': creds.refresh_token,
                'token_uri': creds.token_uri,
                'client_id': creds.client_id,
                'client_secret': creds.client_secret,
                'scopes': creds.scopes
            }
        except RefreshError:
            request.session.pop('credentials', None)
            raise HTTPException(
                status_code=401, 
                detail="Authentication token expired or invalid. Please re-authenticate."
            )
    
    return creds

async def verify_credentials(creds: Credentials, service) -> bool:
    try:
        service.users().getProfile(userId='me').execute()
        return True
    except Exception:
        return False
