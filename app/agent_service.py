import time

from google.api_core.exceptions import ResourceExhausted

from app.llm_service import model


RATE_LIMIT_COOLDOWN_SECONDS = 20
_rate_limited_until = 0.0


TOOLS = {
    "chat": "For greetings, small talk, or messages that are not about emails at all",
    "search_emails": "Find and list specific emails matching a query",
    "summarize_emails": "Create a bullet-point summary of email snippets",
    "answer_question": "Answer questions that require email context",
}

TOOL_DISPLAY = {
    "search_emails": "Searching emails",
    "summarize_emails": "Summarizing emails",
    "answer_question": "Answering from context",
}


def _is_rate_limited() -> bool:
    return time.monotonic() < _rate_limited_until


def _mark_rate_limited() -> None:
    global _rate_limited_until
    _rate_limited_until = time.monotonic() + RATE_LIMIT_COOLDOWN_SECONDS


def _heuristic_tool(user_query: str) -> str:
    query = user_query.lower().strip()
    chat_markers = {
        "hi", "hello", "hey", "thanks", "thank you", "bye", "goodbye", "ok", "okay"
    }
    if query in chat_markers:
        return "chat"
    if any(token in query for token in ["summar", "recap", "overview"]):
        return "summarize_emails"
    if any(token in query for token in ["find", "search", "look for", "from "]):
        return "search_emails"
    if any(token in query for token in ["email", "inbox", "todo", "to-do", "task"]):
        return "answer_question"
    return "answer_question"


def _generate_text_with_retry(prompt: str, max_retries: int = 3) -> tuple[str | None, bool]:
    if _is_rate_limited():
        return None, True

    for attempt in range(max_retries):
        try:
            response = model.generate_content(prompt)
            return (response.text or ""), False
        except ResourceExhausted:
            _mark_rate_limited()
            if attempt == max_retries - 1:
                return None, True
            time.sleep((2 ** attempt) + 1)
        except Exception:
            return None, False
    return None, False


def _select_tool(user_query: str) -> str:
    heuristic_tool = _heuristic_tool(user_query)

    # Skip an extra model call for obvious conversational messages.
    if heuristic_tool == "chat":
        return "chat"

    tool_list = "\n".join(
        f"{i + 1}. {name} - {desc}" for i, (name, desc) in enumerate(TOOLS.items())
    )
    reasoning_prompt = (
        "You are an intelligent email assistant and tool router.\n"
        "Choose exactly one tool based on user intent.\n\n"
        "Routing rules:\n"
        "- Use chat for greetings, pleasantries, acknowledgements, or non-email small talk.\n"
        "- Use summarize_emails when the user asks what is in inbox, wants overview/recap/highlights, asks for today's email summary, or asks for todos inferred from inbox.\n"
        "- Use search_emails when the user wants specific matching emails (sender/topic/keyword lookup).\n"
        "- Use answer_question when the user asks a concrete question that should be answered using email context.\n"
        "- If ambiguous between search_emails and summarize_emails, prefer summarize_emails for broad inbox requests.\n\n"
        f"Available tools:\n{tool_list}\n\n"
        f"User message: {user_query}\n\n"
        "Which tool is best? Respond with ONLY the tool name, nothing else."
    )
    choice_text, rate_limited = _generate_text_with_retry(reasoning_prompt)
    if choice_text is None:
        return heuristic_tool

    choice = choice_text.strip().lower() if choice_text else "answer_question"

    if "chat" in choice:
        return "chat"
    if "search" in choice:
        return "search_emails"
    if "summariz" in choice:
        return "summarize_emails"
    return "answer_question"


def select_tool_for_query(user_query: str) -> str:
    return _select_tool(user_query)


async def agent_loop(
    user_query: str,
    relevant_emails: list[str],
    history_context: str = "",
    selected_tool: str | None = None,
) -> tuple[str, dict]:
    """
    LLM reasons about which tool fits the user message, executes it,
    then generates a final response.
    Returns: (response_text, trace)
    """
    trace: dict = {"tool_used": None, "steps": 1}

    if selected_tool is None:
        selected_tool = _select_tool(user_query)
    trace["tool_used"] = selected_tool

    if selected_tool == "chat":
        prompt = "You are a friendly email assistant. The user is not asking about emails right now.\n"
        if history_context:
            prompt += f"Previous conversation:\n{history_context}\n\n"
        prompt += f"User: {user_query}\n\nRespond naturally and conversationally. Be warm and brief."
        result_text, rate_limited = _generate_text_with_retry(prompt)
        if result_text is None and rate_limited:
            return "I am temporarily rate-limited right now. Please try again in about 20 seconds.", trace
        return (result_text or "Hey! How can I help?"), trace

    if not relevant_emails:
        return "I checked but I could not find usable emails for that request yet.", trace

    trace["steps"] = 2

    if selected_tool == "search_emails":
        prompt = (
            "You are an email assistant. Present results clearly and concisely.\n"
            "Use bullets, prioritize relevance, and avoid speculation.\n\n"
            f"User request: {user_query}\n\n"
            "Matching emails:\n\n"
        )
        for idx, email in enumerate(relevant_emails[:3], 1):
            prompt += f"{idx}. {email[:200]}..\n\n"
        prompt += "Give a concise answer focused on what matches the request."

    elif selected_tool == "summarize_emails":
        prompt = (
            "You are an email summarization assistant.\n"
            "Produce a clear, actionable summary.\n"
            "Prioritize important items, deadlines, asks, and likely todos.\n"
            "If appropriate, include a short 'Suggested To-Dos' section.\n\n"
            f"User request: {user_query}\n\n"
            "Email snippets:\n\n"
        )
        for email in relevant_emails[:5]:
            prompt += f"{email}\n\n---\n\n"
        prompt += "Return a concise summary with practical next steps when available."

    else:
        prompt = (
            "You are an email Q&A assistant.\n"
            "Answer using only the provided email context.\n"
            "If evidence is insufficient, say that clearly and suggest what to ask next.\n\n"
            "Email context:\n"
        )
        for email in relevant_emails[:5]:
            prompt += f"- {email}\n\n"
        prompt += (
            f"User question: {user_query}\n\n"
            "Give a direct answer, then include a brief evidence-based explanation."
        )

    if history_context:
        prompt = f"Previous conversation:\n{history_context}\n\n" + prompt

    result_text, rate_limited = _generate_text_with_retry(prompt)
    if result_text is None and rate_limited:
        return "I hit a temporary rate limit while processing that. Please try again in about 20 seconds.", trace
    return (result_text or "Sorry, I couldn't generate a response."), trace


async def stream_agent_response(
    user_query: str,
    relevant_emails: list[str],
    history_context: str = "",
    selected_tool: str | None = None,
):
    """Stream tool indicator then response text to the client."""
    try:
        response_text, trace = await agent_loop(
            user_query,
            relevant_emails,
            history_context,
            selected_tool=selected_tool,
        )
    except Exception:
        yield "I ran into a temporary issue while processing that request. Please try again."
        return

    if trace["tool_used"] in TOOL_DISPLAY:
        yield f"*[{TOOL_DISPLAY[trace['tool_used']]}]*\n\n"

    for char in response_text:
        yield char
