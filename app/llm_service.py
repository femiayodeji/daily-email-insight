import asyncio
from typing import AsyncGenerator
from app.config import GEMINI_API_KEY, GEMINI_MODEL
import google.generativeai as genai
from google.api_core.exceptions import ResourceExhausted

genai.configure(api_key=GEMINI_API_KEY)

model = genai.GenerativeModel(GEMINI_MODEL)


async def stream_generate_content(query_request: str, max_retries: int = 3) -> AsyncGenerator[str, None]:
    for attempt in range(max_retries):
        try:
            stream = model.generate_content(query_request, stream=True)
            for chunk in stream:
                if hasattr(chunk, 'text') and chunk.text:
                    yield chunk.text
                    # Yield control so the server can flush each chunk to the client.
                    await asyncio.sleep(0)
            return
        except ResourceExhausted:
            if attempt == max_retries - 1:
                yield "⚠️ Rate limit exceeded. Please try again in a few moments."
                return
            await asyncio.sleep((2 ** attempt) + 1)
        except Exception as e:
            yield f"⚠️ An error occurred: {str(e)}"
            return


def create_summary_prompt(email_texts: list[str]) -> str:
    merged_email = "\n\n".join(email_texts)
    merged_email += f"\nEmail count: {len(email_texts)}"
    return f"Summarize this text concisely:\n\n{merged_email}"
