from typing import Dict, List
from collections import defaultdict


class ChatHistory:
    def __init__(self):
        self._history: Dict[str, List[Dict[str, str]]] = defaultdict(list)
    
    def add_message(self, session_id: str, role: str, content: str) -> None:
        self._history[session_id].append({"role": role, "content": content})
    
    def get_history(self, session_id: str, max_messages: int = 10) -> List[Dict[str, str]]:
        history = self._history.get(session_id, [])
        return history[-max_messages:]
    
    def format_history(self, session_id: str, max_messages: int = 10) -> str:
        history = self.get_history(session_id, max_messages)
        if not history:
            return ""
        
        formatted = ["Previous conversation:"]
        for msg in history:
            formatted.append(f"{msg['role'].capitalize()}: {msg['content']}")
        
        return "\n".join(formatted)


chat_history = ChatHistory()
