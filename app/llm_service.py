import asyncio
from app.config import GEMINI_API_KEY, GEMINI_MODEL
import google.generativeai as genai
from google.api_core.exceptions import ResourceExhausted

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel(GEMINI_MODEL)

async def generate_content(query_request: str, max_retries: int = 3) -> str:
    for attempt in range(max_retries):
        try:
            gemini_response = model.generate_content(query_request)
            return gemini_response.text if hasattr(gemini_response, 'text') else str(gemini_response)
        except ResourceExhausted as e:
            if attempt == max_retries - 1:
                raise Exception("Rate limit exceeded. Please try again in a few moments.") from e
            await asyncio.sleep((2 ** attempt) + 1)
        except Exception:
            raise

async def stream_generate_content(query_request: str, max_retries: int = 3):
    for attempt in range(max_retries):
        try:
            if hasattr(model, 'generate_content_stream'):
                stream = model.generate_content_stream(query_request)
                for chunk in stream:
                    yield chunk.text if hasattr(chunk, 'text') else str(chunk)
            else:
                yield await generate_content(query_request)
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


def create_query_prompt(relevant_emails: list[str], user_query: str, chat_history: str = "") -> str:
    context = "\n\n---\n\n".join(relevant_emails)
    
    prompt_parts = []
    if chat_history:
        prompt_parts.append(chat_history)
        prompt_parts.append("")
    
    prompt_parts.append(f"Based on the following emails:\n\n{context}")
    prompt_parts.append(f"\nAnswer this question in a friendly, conversational tone: {user_query}")
    prompt_parts.append("\nImportant guidelines:")
    prompt_parts.append("- Only use information from the emails provided above. If the answer isn't in the emails, politely say so.")
    prompt_parts.append("- Be conversational and natural, but concise.")
    prompt_parts.append("- If the user is just reacting (e.g., 'really?', 'wow', 'cool', 'ok') rather than asking a new question, respond briefly and naturally without repeating information you already shared. You can acknowledge their reaction, add a small new detail if relevant, or ask if they want to know more.")
    prompt_parts.append("- Review the chat history to avoid repeating yourself. If you already explained something, don't repeat the exact same information.")
    prompt_parts.append("- If the user is ending the conversation (e.g., 'goodbye', 'thanks', 'bye', 'see you later'), acknowledge warmly and briefly.")
    
    return "\n".join(prompt_parts)