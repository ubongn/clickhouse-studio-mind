@echo off
rem Local tunnel entrypoint: uvicorn on 127.0.0.1:8100 behind the production
rem Cloudflare Tunnel (studio.sabiedu.online). Env: .env (loaded by config.py,
rem WITHOUT overriding process env) + the Vertex AI overrides below, so the
rem service key (not the dev API key) drives Gemini at runtime.
set "PROVIDER=vertex"
set "GOOGLE_APPLICATION_CREDENTIALS=C:\Users\Sabiedu\.qwenpaw\workspaces\hack_2\vertex-key.json"
set "GOOGLE_CLOUD_PROJECT=agentic-cinema-506710"
set "GOOGLE_CLOUD_LOCATION=us-central1"
set "PYTHONUNBUFFERED=1"
"%~dp0..\.venv\Scripts\python.exe" -m uvicorn studio_mind.server:app --host 127.0.0.1 --port 8100
