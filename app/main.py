import os
import uuid
from io import BytesIO
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from google.oauth2.credentials import Credentials
from starlette.middleware.sessions import SessionMiddleware

from app.config import *
from app.gauth import get_credentials, get_flow, verify_credentials
from app.gmail_service import get_daily_email, get_service
from app.llm_service import stream_generate_content, create_summary_prompt, create_query_prompt
from app.tts_service import use_gtts
from app.vector_service import embed_and_store_emails, query_similar_emails
from app.session_service import chat_history

@asynccontextmanager
async def lifespan(app):
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
async def summary(request: Request, creds: Credentials = Depends(get_credentials)):
    try:
        email_texts = await get_daily_email(creds)
        
        embed_and_store_emails(email_texts)
        
        prompt = create_summary_prompt(email_texts)

        return StreamingResponse(
            stream_generate_content(prompt),
            media_type="text/plain"
        )
    except Exception as e:
        if "Authentication token expired" in str(e) or "invalid_grant" in str(e):
            request.session.pop('credentials', None)
        raise


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
    user_query = data.get('text', '')
    
    if not user_query:
        return {"response": "Please provide a query."}
    
    session_id = request.session.get('session_id')
    if not session_id:
        session_id = str(uuid.uuid4())
        request.session['session_id'] = session_id
    
    relevant_emails = query_similar_emails(user_query, top_k=5)
    
    if not relevant_emails:
        return {"response": "No emails found. Please generate a summary first."}
    
    history_context = chat_history.format_history(session_id, max_messages=6)
    chat_history.add_message(session_id, "user", user_query)
    
    prompt = create_query_prompt(relevant_emails, user_query, history_context)
    
    async def stream_and_save():
        chunks = []
        async for chunk in stream_generate_content(prompt):
            chunks.append(chunk)
            yield chunk
        chat_history.add_message(session_id, "assistant", "".join(chunks))
    
    return StreamingResponse(
        stream_and_save(),
        media_type="text/plain"
    )



@app.get("/auth/check")
async def check_auth(request: Request, creds: Credentials = Depends(get_credentials)):
    is_valid = await verify_credentials(creds, get_service(creds))
    if not is_valid:
        request.session.pop('credentials', None)
        raise HTTPException(status_code=401, detail="Authentication invalid")
    return {"authenticated": True}


@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url='/')