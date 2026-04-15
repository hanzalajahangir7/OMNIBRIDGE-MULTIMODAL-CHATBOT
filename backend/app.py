#!/usr/bin/env python3
import asyncio
import warnings
import base64
import cgi
import json
import mimetypes
import os
import shutil
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse
import requests
import io
from datetime import datetime
import redis

# --- Optional document parsing libs (graceful fallback if not installed) ---
try:
    import docx as _docx  # python-docx
    _HAS_DOCX = True
except ImportError:
    _HAS_DOCX = False

try:
    import PyPDF2 as _pypdf
    _HAS_PDF = True
except ImportError:
    _HAS_PDF = False

warnings.filterwarnings("ignore", category=DeprecationWarning)

# Absolute Path Setup
ROOT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = ROOT_DIR.parent
ENV_FILE = PROJECT_DIR / ".env"

def load_dotenv_early(path: Path) -> None:
    if not path.exists(): return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line: continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ: os.environ[k] = v

load_dotenv_early(ENV_FILE)
FRONTEND_DIR = PROJECT_DIR / "frontend"

if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from auth import (
    AUTH_COOKIE_NAME,
    AuthConfigurationError,
    AuthError,
    AuthValidationError,
    build_google_login_url,
    clear_auth_session,
    complete_google_login,
    create_local_account,
    get_auth_status,
    initialize_auth_store,
    login_local_account,
    logout_auth_session,
    record_guest_message,
)
from database.db import DatabaseError, DatabaseUnavailableError, get_database_status
from database.memory import (
    normalize_user_id,
    persist_assistant_message,
    get_recent_messages,
    prepare_text_memory_context
)

# LOCAL OLLAMA MODELS
OLLAMA_TEXT_MODEL = os.getenv("OLLAMA_TEXT_MODEL", "llama3.2:3b")
OLLAMA_VISION_MODEL = os.getenv("OLLAMA_VISION_MODEL", "moondream")
OLLAMA_FILE_MODEL = os.getenv("OLLAMA_FILE_MODEL", "granite3.2-vision:2b")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
OLLAMA_TAGS_URL = f"{OLLAMA_BASE_URL}/api/tags"
OLLAMA_CHAT_URL = f"{OLLAMA_BASE_URL}/api/chat"
OLLAMA_KEEP_ALIVE = os.getenv("OLLAMA_KEEP_ALIVE", "10m")

def load_system_prompt() -> str:
    prompt_path = ROOT_DIR / "system_prompt.txt"
    if prompt_path.exists():
        return prompt_path.read_text(encoding="utf-8")
    return "You are a polished assistant inside a web app. Keep answers clear, structured, and practical."

DEFAULT_INSTRUCTIONS = load_system_prompt()

@dataclass
class ChatState:
    text_history: list[dict] = field(default_factory=list)
    uploaded_file_names: list[str] = field(default_factory=list)

@dataclass
class SystemProfile:
    cpu_threads: int
    total_ram_gb: float
    available_ram_gb: float

@dataclass
class ChatResult:
    provider: str
    provider_label: str
    model: str
    assistant_text: str
    fallback_used: bool = False
    fallback_reason: str | None = None
    routing_reason: str | None = None
    estimated_tokens: int | None = None
    database_backed: bool = False

@dataclass
class RouteDecision:
    provider: str
    reason: str
    estimated_tokens: int
    model: str | None = None

chat_store: dict[str, ChatState] = {}
chat_store_lock = threading.Lock()
ollama_status_cache = {"value": None, "timestamp": 0.0}

# Redis Setup
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
try:
    redis_client = redis.from_url(REDIS_URL, decode_responses=True)
    HAS_REDIS = True
except Exception:
    HAS_REDIS = False

def get_session_data(session_id: str) -> ChatState | None:
    if not HAS_REDIS: return chat_store.get(session_id)
    try:
        data = redis_client.get(f"session:{session_id}")
        if data:
            raw = json.loads(data)
            return ChatState(text_history=raw.get("text_history", []), uploaded_file_names=raw.get("uploaded_file_names", []))
    except Exception: pass
    return chat_store.get(session_id)

def set_session_data(session_id: str, state: ChatState):
    if not HAS_REDIS:
        chat_store[session_id] = state
        return
    try:
        redis_client.setex(f"session:{session_id}", 3600*24, json.dumps({"text_history": state.text_history, "uploaded_file_names": state.uploaded_file_names}))
    except Exception:
        chat_store[session_id] = state


def extract_text_from_file(content: bytes, filename: str, mime_type: str) -> str:
    """Extract readable text from uploaded documents. Returns text or empty string."""
    name_lower = filename.lower()
    try:
        # Plain text / code files
        if mime_type.startswith("text/") or name_lower.endswith((".txt", ".md", ".csv", ".log", ".json", ".py", ".js", ".ts", ".html", ".css", ".xml", ".yaml", ".yml")):
            return content.decode("utf-8", errors="replace")

        # DOCX
        if name_lower.endswith(".docx") or mime_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
            if _HAS_DOCX:
                doc = _docx.Document(io.BytesIO(content))
                return "\n".join(p.text for p in doc.paragraphs if p.text.strip())
            return "[DOCX file — install python-docx to enable reading: pip install python-docx]"

        # PDF
        if name_lower.endswith(".pdf") or mime_type == "application/pdf":
            if _HAS_PDF:
                reader = _pypdf.PdfReader(io.BytesIO(content))
                pages = []
                for page in reader.pages:
                    text = page.extract_text()
                    if text: pages.append(text)
                return "\n\n".join(pages)
            return "[PDF file — install PyPDF2 to enable reading: pip install PyPDF2]"

    except Exception as ex:
        return f"[Could not extract text from {filename}: {ex}]"
    return ""  # Unsupported binary format
ollama_status_cache_lock = threading.Lock()

def estimate_tokens(text: str) -> int:
    return len(text) // 4

def get_system_profile() -> SystemProfile:
    try:
        import psutil
        return SystemProfile(
            cpu_threads=os.cpu_count() or 1,
            total_ram_gb=psutil.virtual_memory().total / (1024**3),
            available_ram_gb=psutil.virtual_memory().available / (1024**3),
        )
    except (ImportError, AttributeError):
        return SystemProfile(cpu_threads=os.cpu_count() or 1, total_ram_gb=0.0, available_ram_gb=0.0)

def list_ollama_models() -> list[dict]:
    try:
        response = requests.get(OLLAMA_TAGS_URL, timeout=(0.5, 1.0))
        if not response.ok: return []
        return response.json().get("models", [])
    except Exception: return []

def choose_ollama_model(models: list[dict]) -> str:
    wanted = OLLAMA_TEXT_MODEL.lower()
    for m in models:
        mname = str(m.get("name", "")).lower()
        if mname == wanted or mname.startswith(f"{wanted}:"):
            return mname
    return models[0].get("name") if models else OLLAMA_TEXT_MODEL

def get_ollama_runtime(profile: SystemProfile) -> dict:
    with ollama_status_cache_lock:
        if ollama_status_cache["value"] and (time.time() - ollama_status_cache["timestamp"]) < 5:
            return ollama_status_cache["value"]
    models = list_ollama_models()
    info = {"ready": len(models) > 0, "selectedModel": choose_ollama_model(models), "installedModels": [m.get("name") for m in models]}
    with ollama_status_cache_lock:
        ollama_status_cache["value"] = info
        ollama_status_cache["timestamp"] = time.time()
    return info

def build_ollama_messages(text_history: list[dict], user_text: str, custom_system: str | None = None) -> list[dict]:
    sys_content = DEFAULT_INSTRUCTIONS
    if custom_system:
        sys_content = f"{DEFAULT_INSTRUCTIONS}\n\n{custom_system}"
    msgs = [{"role": "system", "content": sys_content}]
    for turn in text_history[-12:]:
        msgs.append({"role": turn["role"], "content": turn["content"]})
    msgs.append({"role": "user", "content": user_text})
    return msgs

def stream_ollama_reply(model: str, messages: list[dict], images: list[str] | None = None) -> str:
    payload = {"model": model, "messages": messages, "stream": True, "keep_alive": OLLAMA_KEEP_ALIVE}
    if images and payload["messages"]: payload["messages"][-1]["images"] = images
    # Always use a generous timeout (300s) for Ollama, as it may need to load models into RAM/VRAM
    read_timeout = int(os.getenv("OLLAMA_READ_TIMEOUT", "300"))
    connect_timeout = 5.0
    try:
        response = requests.post(OLLAMA_CHAT_URL, json=payload, stream=True, timeout=(connect_timeout, read_timeout))
        response.raise_for_status()
        full_text = ""
        for line in response.iter_lines():
            if not line: continue
            chunk = json.loads(line.decode("utf-8"))
            content = chunk.get("message", {}).get("content", "")
            full_text += content
            if chunk.get("done"): break
        if not full_text:
            raise RuntimeError("Ollama returned an empty response. The model may still be loading — please try again in a moment.")
        return full_text
    except requests.exceptions.ConnectTimeout:
        raise RuntimeError("Cannot reach Ollama. Make sure 'ollama serve' is running.")
    except requests.exceptions.ReadTimeout:
        raise RuntimeError(f"Ollama timed out after {read_timeout}s. The model '{model}' may still be loading or the input is too large. Try again shortly.")
    except requests.exceptions.ConnectionError:
        raise RuntimeError("Ollama is not running. Please start it with: ollama serve")
    except Exception as e:
        raise RuntimeError(f"Ollama failed: {str(e)}")

def call_ollama(message: str, state: ChatState, model: str, decision: RouteDecision, images: list[str] | None = None, database_user_id: str | None = None, memory_prompt: str | None = None) -> ChatResult:
    assistant_text = stream_ollama_reply(model, build_ollama_messages(state.text_history, message, custom_system=memory_prompt), images=images)
    with chat_store_lock:
        state.text_history.append({"role": "user", "content": message})
        state.text_history.append({"role": "assistant", "content": assistant_text})
    if database_user_id:
        try: persist_assistant_message(database_user_id, assistant_text)
        except Exception: pass
    return ChatResult("ollama", "Ollama Local", model, assistant_text, routing_reason=decision.reason, estimated_tokens=decision.estimated_tokens, database_backed=bool(database_user_id))

def select_model(message: str, context: dict) -> RouteDecision:
    tokens = estimate_tokens(message)
    if context.get("hasFiles"): return RouteDecision("ollama", "Document Specialist", tokens, OLLAMA_FILE_MODEL)
    if context.get("hasImages"): return RouteDecision("ollama", "Vision Specialist", tokens, OLLAMA_VISION_MODEL)
    return RouteDecision("ollama", "Text Specialist", tokens, OLLAMA_TEXT_MODEL)

def generate_response(message: str, state: ChatState, context: dict) -> ChatResult:
    decision = select_model(message, context)
    return call_ollama(message, state, context.get("ollamaModel") or decision.model, decision, images=context.get("images"), database_user_id=context.get("databaseUserId"), memory_prompt=context.get("memoryPrompt"))

def get_state(session_id: str) -> ChatState:
    state = get_session_data(session_id)
    if state: return state
    return ChatState()

def dump_to_local_folder(user_id, email, message, assist_text, provider):
    folder = ROOT_DIR.parent / "user_data"
    folder.mkdir(exist_ok=True)
    filename = f"{email or user_id}.txt"
    with open(folder / filename, "a", encoding="utf-8") as f:
        f.write(f"--- {datetime.now().isoformat()} [{provider}] ---\nUSER: {message}\nAI: {assist_text}\n\n")

class LocalOnlyHandler(BaseHTTPRequestHandler):
    def ensure_session(self) -> str:
        raw_cookie = self.headers.get("Cookie", "")
        cookies = SimpleCookie(raw_cookie)
        cookie = cookies.get("ollama_desk_session")
        if not cookie:
            sid = uuid.uuid4().hex
            self._session_id = sid
            self._set_cookie = True
            return sid
        self._session_id = cookie.value
        self._set_cookie = False
        return self._session_id

    def send_json(self, payload: dict, status: HTTPStatus = HTTPStatus.OK, extra_headers: list = None) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        if getattr(self, "_set_cookie", False):
            self.send_header("Set-Cookie", f"ollama_desk_session={self._session_id}; Path=/; HttpOnly; SameSite=Lax")
        if extra_headers:
            for k, v in extra_headers:
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def parse_json_body(self) -> dict:
        clen = int(self.headers.get("Content-Length", "0") or "0")
        if clen <= 0: return {}
        try: return json.loads(self.rfile.read(clen).decode("utf-8"))
        except: return {}

    def get_auth_context(self):
        sid = self.ensure_session()
        raw_cookie = self.headers.get("Cookie", "")
        cookies = SimpleCookie(raw_cookie)
        token = cookies.get(AUTH_COOKIE_NAME).value if cookies.get(AUTH_COOKIE_NAME) else None
        return sid, get_auth_status(sid, token)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/status": self.handle_status()
        elif parsed.path == "/api/auth/status": self.handle_auth_status()
        elif parsed.path == "/api/history": self.handle_history()
        elif parsed.path == "/auth/google/start": self.handle_google_start(parsed)
        elif parsed.path == "/auth/google/callback": self.handle_google_callback(parsed)
        elif parsed.path == "/health": self.send_json({"status": "ok", "timestamp": time.time()})
        elif parsed.path.startswith(("/static/", "/src/", "/node_modules/")): self.serve_static(parsed.path)
        else: self.serve_index()

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/chat": self.handle_chat()
        elif parsed.path == "/api/reset": self.handle_reset()
        elif parsed.path == "/api/auth/signup": self.handle_auth_signup()
        elif parsed.path == "/api/auth/login": self.handle_auth_login()
        elif parsed.path == "/api/auth/logout": self.handle_auth_logout()
        else: self.send_json({"ok": False, "error": f"Unknown endpoint: {parsed.path}"}, status=HTTPStatus.NOT_FOUND)

    def handle_status(self):
        prof = get_system_profile()
        runtime = get_ollama_runtime(prof)
        _, auth = self.get_auth_context()
        self.send_json({"ok": runtime["ready"], "activeProvider": "ollama", "activeModel": runtime["selectedModel"], "system": {"cpuThreads": prof.cpu_threads, "totalRamGb": round(prof.total_ram_gb,1), "availableRamGb": round(prof.available_ram_gb,1)}, "ollama": runtime, "auth": auth.as_dict()})

    def handle_auth_status(self):
        _, auth = self.get_auth_context()
        self.send_json({"ok": True, "auth": auth.as_dict()})

    def handle_history(self):
        _, auth = self.get_auth_context()
        if not auth.is_authenticated:
            self.send_json({"ok": True, "messages": []})
            return
        try:
            msgs = get_recent_messages(auth.user_id, limit=50)
            self.send_json({"ok": True, "messages": [{"role": r, "content": t} for r, t in msgs]})
        except DatabaseUnavailableError:
            self.send_json({"ok": True, "messages": [], "warning": "Database unavailable, history not loaded."})
        except Exception as e:
            self.send_json({"ok": True, "messages": [], "warning": str(e)})

    def handle_reset(self):
        sid, _ = self.get_auth_context()
        with chat_store_lock:
            if sid in chat_store:
                chat_store[sid].text_history = []
        self.send_json({"ok": True})

    def handle_auth_signup(self):
        sid, _ = self.get_auth_context()
        try:
            body = self.parse_json_body()
            token, status = create_local_account(body.get("email", ""), body.get("password", ""), sid, display_name=body.get("displayName", ""))
            self.send_json({"ok": True, "auth": status.as_dict()}, extra_headers=[("Set-Cookie", f"{AUTH_COOKIE_NAME}={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age=2592000")])
        except Exception as e:
            self.send_json({"ok": False, "error": str(e)}, status=HTTPStatus.BAD_REQUEST)

    def handle_auth_login(self):
        sid, _ = self.get_auth_context()
        try:
            body = self.parse_json_body()
            token, status = login_local_account(body.get("email", ""), body.get("password", ""), sid)
            self.send_json({"ok": True, "auth": status.as_dict()}, extra_headers=[("Set-Cookie", f"{AUTH_COOKIE_NAME}={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age=2592000")])
        except Exception as e:
            self.send_json({"ok": False, "error": str(e)}, status=HTTPStatus.BAD_REQUEST)

    def handle_auth_logout(self):
        sid, _ = self.get_auth_context()
        token = SimpleCookie(self.headers.get("Cookie", "")).get(AUTH_COOKIE_NAME)
        if token: logout_auth_session(token.value)
        self.send_json({"ok": True, "auth": clear_auth_session(sid).as_dict()}, extra_headers=[("Set-Cookie", f"{AUTH_COOKIE_NAME}=; Path=/; Max-Age=0")])

    def handle_google_start(self, parsed):
        sid = self.ensure_session()
        query = parse_qs(parsed.query)
        next_path = query.get("next", ["/"])[0]
        try:
            url = build_google_login_url(sid, f"http://{self.headers.get('Host')}", next_path=next_path)
            self.send_response(HTTPStatus.FOUND)
            self.send_header("Location", url)
            self.end_headers()
        except Exception as e:
            self.send_json({"error": str(e)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def handle_google_callback(self, parsed):
        sid = self.ensure_session()
        query = parse_qs(parsed.query)
        try:
            token, _, next_p = complete_google_login(sid, query.get("state", [""])[0], query.get("code", [""])[0], f"http://{self.headers.get('Host')}")
            self.send_response(HTTPStatus.FOUND)
            self.send_header("Set-Cookie", f"{AUTH_COOKIE_NAME}={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age=2592000")
            self.send_header("Location", next_p or "/")
            self.end_headers()
        except Exception as e:
            self.send_json({"error": str(e)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def serve_index(self):
        p = FRONTEND_DIR / "index.html"
        if not p.exists():
            self.send_response(HTTPStatus.NOT_FOUND)
            self.end_headers()
            return
        body = p.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def serve_static(self, path):
        p = (FRONTEND_DIR / path.strip("/")).resolve()
        if not p.exists() or not p.is_file():
            self.send_response(HTTPStatus.NOT_FOUND)
            self.end_headers()
            return
        body = p.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mimetypes.guess_type(p.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def handle_chat(self):
        ctype, pdict = cgi.parse_header(self.headers.get('content-type'))
        if ctype != 'multipart/form-data':
            self.send_json({"error": "Invalid content type"}, status=HTTPStatus.BAD_REQUEST)
            return
        form = cgi.FieldStorage(fp=self.rfile, headers=self.headers, environ={'REQUEST_METHOD': 'POST'})
        message = form.getfirst("message", "").strip()
        files = []
        if "files" in form:
            items = form["files"] if isinstance(form["files"], list) else [form["files"]]
            for item in items:
                if item.filename:
                    files.append({"filename": item.filename, "content": item.file.read(), "mime_type": item.type})
        sid, auth = self.get_auth_context()

        # --- GUEST LIMIT CHECK ---
        if not auth.is_authenticated:
            if auth.guest_messages_remaining <= 0:
                self.send_json({
                    "ok": False,
                    "error": "Guest message limit reached! 🛡️\nPlease Sign Up or Login to continue chatting with OMNIBRIDGE.",
                    "limitReached": True,
                    "auth": auth.as_dict()
                }, status=HTTPStatus.FORBIDDEN)
                return
        # -------------------------

        state = get_state(sid)
        prof = get_system_profile()
        runtime = get_ollama_runtime(prof)

        # Separate images from documents
        img_files = [f for f in files if f["mime_type"].startswith("image/")]
        doc_files = [f for f in files if not f["mime_type"].startswith("image/")]
        has_imgs = bool(img_files)
        has_docs = bool(doc_files)

        # Extract text from documents — keep within llama3.2:3b's 4096-token context
        # ~2500 chars ≈ 625 tokens, safe with system prompt + history + response budget
        MAX_DOC_CHARS = int(os.getenv("MAX_DOC_CHARS", "10000"))
        effective_message = message
        if doc_files:
            extracted_parts = []
            for f in doc_files:
                text = extract_text_from_file(f["content"], f["filename"], f["mime_type"])
                if text:
                    truncated = text[:MAX_DOC_CHARS]
                    truncation_note = f"\n\n[Note: Document truncated to {MAX_DOC_CHARS} chars to fit model context. Full doc has {len(text)} chars.]" if len(text) > MAX_DOC_CHARS else ""
                    extracted_parts.append(f"--- Content of '{f['filename']}' ---\n{truncated}{truncation_note}")
                else:
                    extracted_parts.append(f"['{f['filename']}' is a binary file that cannot be read as text]")
            if extracted_parts:
                effective_message = (message + "\n\n" if message else "") + "\n\n".join(extracted_parts)

        # Route: images → vision model, docs → text model, both → vision model
        decision = select_model(effective_message, {"hasFiles": False, "hasImages": has_imgs})
        imgs_b64 = [base64.b64encode(f["content"]).decode("utf-8") for f in img_files]

        try:
            db_ctx = None
            try:
                db_ctx = prepare_text_memory_context(auth.user_id or sid, effective_message, email=auth.email)
            except DatabaseUnavailableError:
                pass

            res = generate_response(effective_message, state, {
                "ollamaReady": runtime["ready"], "ollamaModel": decision.model,
                "images": imgs_b64, "databaseUserId": db_ctx.user_id if db_ctx else None,
                "memoryPrompt": db_ctx.prompt if db_ctx else None
            })
            
            dump_to_local_folder(auth.user_id or sid, auth.email, message, res.assistant_text, "ollama")
            
            if not auth.is_authenticated:
                try: record_guest_message(sid)
                except Exception: pass

            self.send_json({
                "ok": True, "assistantMessage": res.assistant_text, "userMessage": message, 
                "provider": "ollama", "model": res.model, "routingReason": res.routing_reason,
                "auth": auth.as_dict()
            })
            # Persist state back to Redis/Memory
            set_session_data(sid, state)
        except DatabaseUnavailableError:
            res = generate_response(message, state, {
                "ollamaReady": runtime["ready"], "ollamaModel": decision.model,
                "images": imgs_b64, "databaseUserId": None, "memoryPrompt": None
            })
            warn_msg = "\n\n*(Note: System performance drivers are still initializing. Full memory and personalization will be restored in 30 seconds. Try again soon!)*"
            self.send_json({
                "ok": True, "assistantMessage": res.assistant_text + warn_msg, "userMessage": message, 
                "provider": "ollama", "model": res.model, "routingReason": res.routing_reason,
                "auth": auth.as_dict()
            })
        except Exception as e:
            self.send_json({"error": str(e)}, status=HTTPStatus.BAD_GATEWAY)

def main():
    initialize_auth_store()
    server = ThreadingHTTPServer(("0.0.0.0", 5000), LocalOnlyHandler)
    print("Ollama-Only OMNIBRIDGE running at http://0.0.0.0:5000")
    try: server.serve_forever()
    except KeyboardInterrupt: pass

if __name__ == "__main__":
    main()
