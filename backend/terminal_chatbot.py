#!/usr/bin/env python3
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib import error, request


ROOT_DIR = Path(__file__).resolve().parent
ENV_FILE = ROOT_DIR.parent / ".env"
API_KEY_NAMES = ("GEMINI_API_KEY", "GOOGLE_API_KEY")
MODEL_NAME_OPTIONS = ("GEMINI_MODEL", "GOOGLE_MODEL")
MODELS_URL = "https://generativelanguage.googleapis.com/v1beta/models?pageSize=1000"
GENERATE_URL_TEMPLATE = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
DEFAULT_INSTRUCTIONS = (
    "You are a helpful terminal chatbot. Keep answers clear, practical, and short."
)
PREFERRED_MODELS = (
    "gemini-2.0-flash-lite",
    "gemini-2.0-flash",
    "gemini-2.5-flash-lite-preview-09-2025",
    "gemini-2.5-flash",
    "gemini-1.5-flash",
)
EXCLUDED_MODEL_HINTS = (
    "image",
    "embedding",
    "aqa",
    "tts",
    "live",
    "vision",
)


@dataclass
class ApiResponse:
    payload: dict
    headers: dict[str, str]


class ApiRequestError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")

        if key and key not in os.environ:
            os.environ[key] = value


def get_first_env_value(names: tuple[str, ...]) -> tuple[str | None, str | None]:
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return name, value
    return None, None


def normalize_model_name(model: dict) -> str | None:
    base_model = model.get("baseModelId")
    if isinstance(base_model, str) and base_model.strip():
        return base_model.strip()

    name = model.get("name")
    if isinstance(name, str) and name.startswith("models/"):
        return name.split("/", 1)[1]

    return None


def parse_error_message(body: str) -> str | None:
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return None

    message = payload.get("error", {}).get("message")
    if isinstance(message, str) and message.strip():
        return message.strip()
    return None


def format_header_diagnostics(headers: dict[str, str]) -> str:
    details = []
    request_id = headers.get("x-request-id")
    upload_id = headers.get("x-guploader-uploadid")

    if request_id:
        details.append(f"Request ID: {request_id}")
    if upload_id:
        details.append(f"Upload ID: {upload_id}")

    return " | ".join(details)


def api_request(api_key: str, url: str, method: str = "GET", payload: dict | None = None) -> ApiResponse:
    data = None
    headers = {"x-goog-api-key": api_key}

    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = request.Request(url, data=data, headers=headers, method=method)

    try:
        with request.urlopen(req, timeout=45) as response:
            return ApiResponse(
                payload=json.load(response),
                headers={key.lower(): value for key, value in response.headers.items()},
            )
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="ignore")
        message = parse_error_message(body) or body
        status = exc.code

        if status == 400:
            raise ApiRequestError(f"Gemini API rejected the request. API said: {message}", status_code=status) from None
        if status == 403:
            raise ApiRequestError(f"Gemini API key is invalid or blocked. API said: {message}", status_code=status) from None
        if status == 429:
            raise ApiRequestError(f"Gemini API rate limit or quota exceeded. API said: {message}", status_code=status) from None

        raise ApiRequestError(f"Gemini API returned HTTP {status}. API said: {message}", status_code=status) from None
    except error.URLError as exc:
        raise ApiRequestError(f"Network error while reaching Gemini API: {exc.reason}") from None


def list_models(api_key: str) -> tuple[list[str], dict[str, str]]:
    response = api_request(api_key, MODELS_URL)
    available = []

    for model in response.payload.get("models", []):
        model_name = normalize_model_name(model)
        methods = model.get("supportedGenerationMethods", [])

        if model_name and "generateContent" in methods:
            available.append(model_name)

    return sorted(set(available)), response.headers


def is_general_text_model(model_name: str) -> bool:
    lowered = model_name.lower()

    if not (lowered.startswith("gemini-") or lowered.startswith("gemma-")):
        return False

    return not any(hint in lowered for hint in EXCLUDED_MODEL_HINTS)


def pick_model(available_models: list[str], requested_model: str | None) -> str:
    if requested_model:
        return requested_model

    available_set = set(available_models)
    for model in PREFERRED_MODELS:
        if model in available_set:
            return model

    general_models = [model for model in available_models if is_general_text_model(model)]
    if not general_models:
        raise RuntimeError("No Gemini text-generation model was found for this API key.")

    return general_models[0]


def extract_text(payload: dict) -> str:
    texts: list[str] = []

    for candidate in payload.get("candidates", []):
        content = candidate.get("content", {})
        for part in content.get("parts", []):
            text = part.get("text")
            if text:
                texts.append(text)

    if texts:
        return "\n".join(texts).strip()

    feedback = payload.get("promptFeedback", {})
    block_reason = feedback.get("blockReason")
    if block_reason:
        return f"[No text returned. Block reason: {block_reason}]"

    return "[No text returned.]"


def generate_url(model: str) -> str:
    return GENERATE_URL_TEMPLATE.format(model=model)


def run_chat(api_key: str, model: str) -> int:
    history: list[dict] = []
    print(f"Connected with Gemini API using model: {model}")
    print("Type your message. Use /reset to clear the conversation, or /quit to exit.\n")

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye!")
            return 0

        if not user_input:
            continue

        lowered = user_input.lower()
        if lowered in {"quit", "exit", "/quit", "/exit"}:
            print("Bye!")
            return 0
        if lowered == "/reset":
            history = []
            print("Conversation reset.\n")
            continue

        history.append({"role": "user", "parts": [{"text": user_input}]})
        payload = {
            "system_instruction": {"parts": [{"text": DEFAULT_INSTRUCTIONS}]},
            "contents": history,
        }

        try:
            response = api_request(api_key, generate_url(model), method="POST", payload=payload)
        except ApiRequestError as exc:
            print(f"Error: {exc}")
            history.pop()
            continue

        assistant_text = extract_text(response.payload)
        history.append({"role": "model", "parts": [{"text": assistant_text}]})
        print(f"Bot: {assistant_text}\n")


def main() -> int:
    load_dotenv(ENV_FILE)
    env_name, api_key = get_first_env_value(API_KEY_NAMES)

    if not api_key:
        print("No Gemini API key found.")
        print("Add GEMINI_API_KEY=your_key_here or GOOGLE_API_KEY=your_key_here to .env")
        return 1

    _, requested_model = get_first_env_value(MODEL_NAME_OPTIONS)

    try:
        available_models, model_headers = list_models(api_key)
        model = pick_model(available_models, requested_model)
    except (RuntimeError, ApiRequestError) as exc:
        print(f"Error: {exc}")
        return 1

    print(f"Loaded key from {env_name}")
    if requested_model:
        print(f"Requested model: {requested_model}")
    else:
        print(f"Auto-selected model: {model}")

    diagnostics = format_header_diagnostics(model_headers)
    if diagnostics:
        print(diagnostics)
    print()

    return run_chat(api_key, model)


if __name__ == "__main__":
    sys.exit(main())
