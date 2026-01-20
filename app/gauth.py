from app.config import GOOGLE_CLIENT_SECRETS, GOOGLE_REDIRECT_URI, SCOPES
from fastapi import Request, HTTPException
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow

import os

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
    return Credentials(**creds_data)
