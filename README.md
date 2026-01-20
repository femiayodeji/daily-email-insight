# Daily Email Insight

Daily Email Insight is a minimal, modern web app that summarizes your daily Gmail inbox using Google OAuth and Gemini AI. It features secure login, streaming summaries, and text-to-speech, all deployable with Docker.

## Features
- Google OAuth login (secure, session-based)
- Summarizes only today's emails
- Gemini AI for concise summaries
- Text-to-speech playback
- Minimal, responsive UI
- Dockerized for easy deployment

---

## Getting Started

### 1. Clone the Repository

```
git clone https://github.com/yourusername/daily-email-insight.git
cd daily-email-insight
```

### 2. Set Up Google OAuth Credentials

1. Go to the [Google Cloud Console](https://console.cloud.google.com/apis/credentials).
2. Create a new project (or select an existing one).
3. Enable the Gmail API for your project.
4. Create OAuth 2.0 Client ID credentials:
   - Application type: Web application
   - Authorized redirect URI: `http://localhost:8000/callback`
5. Download the `credentials.json` file and place it in the project root.

### 3. Get a Gemini API Key

1. Go to the [Google AI Studio](https://aistudio.google.com/app/apikey) (Gemini API).
2. Generate an API key.
3. Copy the key for use in your `.env` file.

### 4. Configure Environment Variables

Copy the example file and fill in your secrets:

```
cp .env.example .env
```

Edit `.env` and set:
- `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` (from Google Cloud Console)
- `GOOGLE_CLIENT_SECRETS=credentials.json`
- `GOOGLE_REDIRECT_URI=http://localhost:8000/callback`
- `GEMINI_API_KEY` (from Google AI Studio)
- `SECRET_KEY` (any random string)

### 5. Build and Run with Docker

```
docker-compose up --build
```

The app will be available at [http://localhost:8000](http://localhost:8000)

---

## Troubleshooting
- Make sure your Google Cloud project has Gmail API enabled.
- The `credentials.json` file must match your OAuth client and redirect URI.
- If you change the port or domain, update the redirect URI in both Google Cloud and `.env`.
- For Gemini API, ensure your key is active and has sufficient quota.

---

## License
MIT
