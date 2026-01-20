
import os
from app.config import GEMINI_API_KEY, GEMINI_MODEL
import google.generativeai as genai

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel(GEMINI_MODEL)

async def generate_content(query_request: str) -> str:
    gemini_response = model.generate_content(f"{query_request}")
    result = gemini_response.text if hasattr(gemini_response, 'text') else str(gemini_response)
    return result

async def stream_generate_content(query_request: str):
    if hasattr(model, 'generate_content_stream'):
        stream = model.generate_content_stream(f"{query_request}")
        for chunk in stream:
            text = chunk.text if hasattr(chunk, 'text') else str(chunk)
            yield text
    else:
        result = await generate_content(query_request)
        yield result