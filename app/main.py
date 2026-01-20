import os
from app.config import *
from app.gauth import get_credentials, get_flow
from app.gmail_service import get_daily_email
from app.llm_service import generate_content, stream_generate_content
from app.tts_service import use_gtts, use_pyttsx3
from fastapi import FastAPI, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from google.oauth2.credentials import Credentials
from starlette.middleware.sessions import SessionMiddleware
from fastapi.responses import StreamingResponse
from fastapi.responses import StreamingResponse as FastAPIStreamingResponse

from io import BytesIO

import os
import sys

from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app):
    print("[INFO] FastAPI server is starting up...", file=sys.stderr)
    print("[INFO] If running with uvicorn, default port is 8000 unless specified otherwise.", file=sys.stderr)
    yield

app = FastAPI(lifespan=lifespan)
app.add_middleware(
    SessionMiddleware,
    secret_key=os.environ.get("SECRET_KEY", "my-secret-key-change-this"),
    max_age=60 * 60 * 24 * 30,
    same_site="lax",
    https_only=False
)


@app.get("/")
async def home():
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

    with open(os.path.join(BASE_DIR, "..", "index.html")) as f:
        return HTMLResponse(content=f.read())

async def getDailyEmailSummary(creds: Credentials):
    email_texts = await get_daily_email(creds)
    merged_email = "\n\n".join(email_texts)
    merged_email += f"\nEmail count: {len(email_texts)}"

    summary = await generate_content(f"Summarize this text concisely:\n\n{merged_email}")
    return summary



@app.get("/login")
async def login(request: Request):
    creds_data = request.session.get('credentials')
    if creds_data:
        creds = Credentials(**creds_data)
        if creds and creds.valid and not creds.expired:
            return RedirectResponse(url="/")
    flow = get_flow()
    auth_url, _ = flow.authorization_url(prompt='consent')
    return RedirectResponse(auth_url)


@app.get("/callback")
async def callback(request: Request, code: str):
    flow = get_flow()
    flow.fetch_token(code=code)
    credentials = flow.credentials
    request.session['credentials'] = {
        'token': credentials.token,
        'refresh_token': credentials.refresh_token,
        'token_uri': credentials.token_uri,
        'client_id': credentials.client_id,
        'client_secret': credentials.client_secret,
        'scopes': credentials.scopes
    }
    return RedirectResponse(url='/')


@app.get("/summary")
async def summary(creds: Credentials = Depends(get_credentials)):
    email_texts = await get_daily_email(creds)
    merged_email = "\n\n".join(email_texts)
    merged_email += f"\nEmail count: {len(email_texts)}"

    prompt = f"Summarize this text concisely:\n\n{merged_email}"

    return FastAPIStreamingResponse(
        stream_generate_content(prompt),
        media_type="text/plain"
    )


@app.post("/tts")
async def text_to_speech(request: Request):
    data = await request.json()
    text = data.get('text', '')

    audio_bytes = use_gtts(text)
    media_type = "audio/mpeg"

    fp = BytesIO(audio_bytes)
    return StreamingResponse(fp, media_type=media_type)


@app.post("/process")
async def process_text(request: Request):
    data = await request.json()
    user_text = data.get('text', '')

    return {"response": f"Processed text: {user_text}"}


@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url='/')