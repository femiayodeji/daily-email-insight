import os
import uuid
import base64
import time
import logging
from io import BytesIO
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from google.oauth2.credentials import Credentials
from starlette.middleware.sessions import SessionMiddleware

from app.config import *
from app.gauth import get_credentials, get_flow, verify_credentials
from app.gmail_service import get_daily_email, get_service
from app.llm_service import (
    stream_generate_content,
    create_summary_prompt,
)
from app.agent_service import stream_agent_response, select_tool_for_query
from app.tts_service import use_gtts
from app.vector_service import embed_and_store_emails, query_similar_emails
from app.session_service import chat_history


logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app):
    # Startup: materialize OAuth client secrets only when provided via env.
    creds = os.getenv("CREDENTIALS_JSON")
    if creds:
        decoded = base64.b64decode(creds).decode()
        os.makedirs(os.path.dirname(GOOGLE_CLIENT_SECRETS) or ".", exist_ok=True)
        with open(GOOGLE_CLIENT_SECRETS, "w") as f:
            f.write(decoded)
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
    client_config = flow.client_config or {}
    client_info = client_config.get('installed', {})
    request.session['credentials'] = {
        'token': getattr(credentials, 'token', None),
        'refresh_token': getattr(credentials, 'refresh_token', None),
        'token_uri': getattr(credentials, 'token_uri', None) or client_info.get('token_uri'),
        'client_id': client_info.get('client_id', ''),
        'client_secret': client_info.get('client_secret', ''),
        'scopes': getattr(credentials, 'scopes', SCOPES)
    }
    return RedirectResponse(url='/')


@app.get("/summary")
async def summary(request: Request, creds: Credentials = Depends(get_credentials)):
    try:
        email_texts = get_daily_email(creds)
        
        embed_and_store_emails(email_texts)
        
        prompt = create_summary_prompt(email_texts)

        async def summary_stream():
            async for chunk in stream_generate_content(prompt):
                yield chunk

        return StreamingResponse(
            summary_stream(),
            media_type="text/plain; charset=utf-8",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
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
async def process_text(request: Request, creds: Credentials = Depends(get_credentials)):
    data = await request.json()
    user_query = data.get('text', '')
    
    if not user_query:
        return {"response": "Please provide a query."}
    
    session_id = request.session.get('session_id')
    if not session_id:
        session_id = str(uuid.uuid4())
        request.session['session_id'] = session_id
    
    try:
        selected_tool = select_tool_for_query(user_query)
    except Exception:
        selected_tool = "answer_question"
    relevant_emails = query_similar_emails(user_query, top_k=5)
    auto_loaded_emails = False

    if not relevant_emails and selected_tool != "chat":
        email_texts = get_daily_email(creds)
        if email_texts:
            embed_and_store_emails(email_texts)
            relevant_emails = query_similar_emails(user_query, top_k=5)
            auto_loaded_emails = True

    history_context = chat_history.format_history(session_id, max_messages=6)
    chat_history.add_message(session_id, "user", user_query)

    async def stream_response():
        started_at = time.perf_counter()

        response_text = ""
        async for chunk in stream_agent_response(
            user_query,
            relevant_emails,
            history_context,
            selected_tool=selected_tool,
        ):
            response_text += chunk
            yield chunk
        
        chat_history.add_message(session_id, "assistant", response_text)
        
        latency_ms = int((time.perf_counter() - started_at) * 1000)
        logger.info(
            "agent_process tool=%s auto_loaded=%s user_query=%s latency_ms=%s session_id=%s",
            selected_tool,
            auto_loaded_emails,
            user_query[:50],
            latency_ms,
            session_id,
        )
    
    return StreamingResponse(
        stream_response(),
        media_type="text/plain",
        headers={"X-Agent-Tool": selected_tool},
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