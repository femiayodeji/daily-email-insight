import os
import uuid
import base64
import time
import logging
import json
import asyncio
from io import BytesIO
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Depends, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from google.oauth2.credentials import Credentials
from starlette.middleware.sessions import SessionMiddleware
from starlette.concurrency import run_in_threadpool

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


def _spawn_embedding_task(email_texts: list[str], source: str) -> None:
    if not email_texts:
        return

    async def _embed_worker() -> None:
        started_at = time.perf_counter()
        try:
            updated = await run_in_threadpool(embed_and_store_emails, email_texts)
            latency_ms = int((time.perf_counter() - started_at) * 1000)
            logger.info(
                "embed_refresh source=%s updated=%s count=%s latency_ms=%s",
                source,
                updated,
                len(email_texts),
                latency_ms,
            )
        except Exception as e:
            logger.warning("embed_refresh_failed source=%s error=%s", source, str(e))

    asyncio.create_task(_embed_worker())


def _extract_sentence_chunks(buffer: str) -> tuple[list[str], str]:
    chunks = []
    start = 0
    for idx, char in enumerate(buffer):
        if char in ".!?\n":
            candidate = buffer[start:idx + 1].strip()
            if candidate:
                chunks.append(candidate)
            start = idx + 1
    return chunks, buffer[start:]


async def _ws_send_json(websocket: WebSocket, send_lock: asyncio.Lock, payload: dict):
    async with send_lock:
        await websocket.send_json(payload)


async def _ws_send_bytes(websocket: WebSocket, send_lock: asyncio.Lock, payload: bytes):
    async with send_lock:
        await websocket.send_bytes(payload)


def _build_ws_credentials(websocket: WebSocket):
    session = websocket.scope.get("session") or {}
    creds_data = session.get("credentials")
    if not creds_data:
        return None
    try:
        return Credentials(**creds_data)
    except Exception:
        return None


async def _run_voice_turn(
    websocket: WebSocket,
    send_lock: asyncio.Lock,
    session_id: str,
    turn_id: str,
    user_query: str,
    cancel_event: asyncio.Event,
):
    try:
        selected_tool = select_tool_for_query(user_query)
    except Exception:
        selected_tool = "answer_question"

    relevant_emails = query_similar_emails(user_query, top_k=5)
    auto_loaded_emails = False

    creds = _build_ws_credentials(websocket)
    if not relevant_emails and selected_tool != "chat" and creds:
        email_texts = await run_in_threadpool(get_daily_email, creds)
        if email_texts:
            # Start answering immediately from fresh mailbox data; refresh embeddings in background.
            relevant_emails = email_texts[:5]
            _spawn_embedding_task(email_texts, "ws_voice")
            auto_loaded_emails = True

    history_context = chat_history.format_history(session_id, max_messages=6)
    chat_history.add_message(session_id, "user", user_query)

    await _ws_send_json(
        websocket,
        send_lock,
        {
            "type": "turn_started",
            "turn_id": turn_id,
            "selected_tool": selected_tool,
        },
    )

    response_chunks = []
    tts_queue: asyncio.Queue[str | None] = asyncio.Queue()
    sentence_buffer = ""
    started_at = time.perf_counter()

    async def llm_producer():
        nonlocal sentence_buffer
        async for chunk in stream_agent_response(
            user_query,
            relevant_emails,
            history_context,
            selected_tool=selected_tool,
        ):
            if cancel_event.is_set():
                break

            response_chunks.append(chunk)
            await _ws_send_json(
                websocket,
                send_lock,
                {
                    "type": "llm_delta",
                    "turn_id": turn_id,
                    "delta": chunk,
                },
            )

            sentence_buffer += chunk
            chunks, sentence_buffer = _extract_sentence_chunks(sentence_buffer)
            for sentence in chunks:
                if not cancel_event.is_set():
                    await tts_queue.put(sentence)

        if sentence_buffer.strip() and not cancel_event.is_set():
            await tts_queue.put(sentence_buffer.strip())

        await tts_queue.put(None)

    async def tts_consumer():
        segment_index = 0
        while True:
            sentence = await tts_queue.get()
            if sentence is None:
                break
            if cancel_event.is_set():
                continue

            try:
                audio_bytes = await run_in_threadpool(use_gtts, sentence)
            except Exception as e:
                await _ws_send_json(
                    websocket,
                    send_lock,
                    {
                        "type": "error",
                        "turn_id": turn_id,
                        "stage": "tts",
                        "message": str(e),
                    },
                )
                continue

            if cancel_event.is_set():
                continue

            await _ws_send_json(
                websocket,
                send_lock,
                {
                    "type": "tts_chunk_meta",
                    "turn_id": turn_id,
                    "segment_index": segment_index,
                    "mime": "audio/mpeg",
                    "text": sentence,
                    "size": len(audio_bytes),
                },
            )
            await _ws_send_bytes(websocket, send_lock, audio_bytes)
            segment_index += 1

        await _ws_send_json(
            websocket,
            send_lock,
            {
                "type": "tts_done",
                "turn_id": turn_id,
                "interrupted": cancel_event.is_set(),
            },
        )

    await asyncio.gather(llm_producer(), tts_consumer())

    full_text = "".join(response_chunks)
    if full_text and not cancel_event.is_set():
        chat_history.add_message(session_id, "assistant", full_text)

    latency_ms = int((time.perf_counter() - started_at) * 1000)
    logger.info(
        "voice_turn tool=%s auto_loaded=%s turn_id=%s interrupted=%s latency_ms=%s session_id=%s",
        selected_tool,
        auto_loaded_emails,
        turn_id,
        cancel_event.is_set(),
        latency_ms,
        session_id,
    )

    await _ws_send_json(
        websocket,
        send_lock,
        {
            "type": "llm_done",
            "turn_id": turn_id,
            "interrupted": cancel_event.is_set(),
            "full_text": full_text,
        },
    )


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
        email_texts = await run_in_threadpool(get_daily_email, creds)
        _spawn_embedding_task(email_texts, "summary")

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

    audio_bytes = await run_in_threadpool(use_gtts, text)
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
        email_texts = await run_in_threadpool(get_daily_email, creds)
        if email_texts:
            # Do not block user response on embeddings; use current mailbox data now.
            relevant_emails = email_texts[:5]
            _spawn_embedding_task(email_texts, "process")
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


@app.websocket("/ws/voice")
async def ws_voice(websocket: WebSocket):
    await websocket.accept()

    session = websocket.scope.get("session")
    if session is None:
        await websocket.close(code=4400)
        return

    session_id = session.get("session_id")
    if not session_id:
        session_id = str(uuid.uuid4())
        session["session_id"] = session_id

    send_lock = asyncio.Lock()
    active_task: asyncio.Task | None = None
    active_cancel_event: asyncio.Event | None = None
    active_turn_id: str | None = None

    async def interrupt_active_turn():
        nonlocal active_task, active_cancel_event, active_turn_id
        if active_task and not active_task.done():
            if active_cancel_event:
                active_cancel_event.set()
            active_task.cancel()
            try:
                await active_task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
            await _ws_send_json(
                websocket,
                send_lock,
                {
                    "type": "interrupted",
                    "turn_id": active_turn_id,
                },
            )
        active_task = None
        active_cancel_event = None
        active_turn_id = None

    await _ws_send_json(websocket, send_lock, {"type": "ready", "session_id": session_id})

    try:
        while True:
            message = await websocket.receive()

            if message.get("type") == "websocket.disconnect":
                break

            if message.get("text") is not None:
                try:
                    payload = json.loads(message["text"])
                except json.JSONDecodeError:
                    await _ws_send_json(
                        websocket,
                        send_lock,
                        {"type": "error", "message": "Invalid JSON payload."},
                    )
                    continue

                msg_type = payload.get("type")
                if msg_type == "interrupt":
                    await interrupt_active_turn()
                    continue

                if msg_type == "ping":
                    await _ws_send_json(websocket, send_lock, {"type": "pong"})
                    continue

                if msg_type != "user_text":
                    await _ws_send_json(
                        websocket,
                        send_lock,
                        {"type": "error", "message": "Unsupported message type."},
                    )
                    continue

                user_query = (payload.get("text") or "").strip()
                if not user_query:
                    await _ws_send_json(
                        websocket,
                        send_lock,
                        {"type": "error", "message": "Text cannot be empty."},
                    )
                    continue

                await interrupt_active_turn()

                turn_id: str = payload.get("turn_id") or str(uuid.uuid4())
                active_turn_id = turn_id
                active_cancel_event = asyncio.Event()
                cancel_event = active_cancel_event
                turn_query = user_query

                async def run_turn():
                    try:
                        await _run_voice_turn(
                            websocket,
                            send_lock,
                            session_id,
                            turn_id,
                            turn_query,
                            cancel_event,
                        )
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        await _ws_send_json(
                            websocket,
                            send_lock,
                            {
                                "type": "error",
                                "turn_id": turn_id,
                                "stage": "voice_turn",
                                "message": str(e),
                            },
                        )

                active_task = asyncio.create_task(run_turn())
                continue

            if message.get("bytes") is not None:
                continue

    except WebSocketDisconnect:
        pass
    finally:
        if active_task and not active_task.done():
            if active_cancel_event:
                active_cancel_event.set()
            active_task.cancel()
            try:
                await active_task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass