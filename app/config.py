import os
from dotenv import load_dotenv

load_dotenv()


def _resolve_google_client_secrets() -> str:
	inline_credentials = os.getenv("CREDENTIALS_JSON")
	configured_path = os.getenv("GOOGLE_CLIENT_SECRETS")

	if not inline_credentials:
		return configured_path or "credentials.json"

	if not configured_path:
		return "/tmp/credentials.json"

	if os.path.abspath(configured_path) == os.path.abspath("credentials.json"):
		return "/tmp/credentials.json"

	return configured_path

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")
GOOGLE_CLIENT_SECRETS = _resolve_google_client_secrets()
GOOGLE_REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI", "http://localhost:8000/callback")
SCOPES = os.getenv("SCOPES", "https://www.googleapis.com/auth/gmail.readonly").split(",")
SECRET_KEY = os.getenv("SECRET_KEY", "my-secret-key-change-this")