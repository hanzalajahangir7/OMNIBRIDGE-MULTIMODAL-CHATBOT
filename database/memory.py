from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import dataclass, field

import requests

from .db import DatabaseError, db_query


GEMINI_API_KEY_NAMES = ("GEMINI_API_KEY", "GOOGLE_API_KEY")
GEMINI_GENERATE_URL_TEMPLATE = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


@dataclass
class StructuredUserProfile:
    communication_style: str = "unknown"
    expertise_level: str = "unknown"
    preferred_tone: str = "clear and practical"
    interests: list[str] = field(default_factory=list)
    goals: list[str] = field(default_factory=list)
    summary: str = "No profile yet."
    last_updated: str | None = None


@dataclass
class HybridMemoryContext:
    user_id: str
    prompt: str
    profile_summary: str
    relevant_memories: list[str]
    recent_messages: list[tuple[str, str]]
    user_profile: StructuredUserProfile
    behavior_instructions: list[str]
    memory_stored: bool = False
    profile_updated: bool = False


class MemoryServiceError(RuntimeError):
    pass


def get_first_env_value(names: tuple[str, ...]) -> tuple[str | None, str | None]:
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return name, value
    return None, None


def get_ollama_url() -> str:
    return os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")


def get_embedding_model() -> str:
    return os.getenv("OLLAMA_EMBEDDING_MODEL", "nomic-embed-text")


def get_summary_model() -> str:
    return os.getenv("OLLAMA_MEMORY_SUMMARIZER_MODEL", os.getenv("OLLAMA_MODEL", "qwen2.5:0.5b"))


def get_profile_model() -> str:
    return os.getenv("OLLAMA_PROFILE_MODEL", get_summary_model())


def get_profile_gemini_model() -> str:
    return os.getenv("PROFILE_GEMINI_MODEL", os.getenv("GEMINI_MODEL", "gemini-2.5-flash"))


def get_recent_message_limit() -> int:
    return max(1, int(os.getenv("DB_RECENT_MESSAGE_LIMIT", "5")))


def get_memory_limit() -> int:
    return max(1, int(os.getenv("DB_MEMORY_LIMIT", "5")))


def get_profile_refresh_every() -> int:
    return max(1, int(os.getenv("USER_PROFILE_REFRESH_EVERY_MESSAGES", "4")))


def get_profile_source_message_limit() -> int:
    return max(4, int(os.getenv("USER_PROFILE_SOURCE_MESSAGE_LIMIT", "16")))


def get_profile_max_list_items() -> int:
    return max(2, int(os.getenv("USER_PROFILE_MAX_LIST_ITEMS", "8")))


def normalize_user_id(raw_user_id: str) -> str:
    try:
        return str(uuid.UUID(raw_user_id))
    except ValueError:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, raw_user_id))


def vector_literal(values: list[float]) -> str:
    return "[" + ",".join(f"{float(value):.10f}" for value in values) + "]"


def get_embedding(text: str) -> list[float]:
    response = requests.post(
        f"{get_ollama_url()}/api/embeddings",
        json={"model": get_embedding_model(), "prompt": text},
        timeout=60,
    )
    if not response.ok:
        raise MemoryServiceError(
            f"Ollama embedding request failed ({response.status_code}): {response.text.strip() or 'Unknown error.'}"
        )

    payload = response.json()
    embedding = payload.get("embedding")
    if not isinstance(embedding, list) or not embedding:
        raise MemoryServiceError("Ollama did not return a usable embedding vector.")

    return [float(value) for value in embedding]


def summarize_memory(text: str) -> str:
    response = requests.post(
        f"{get_ollama_url()}/api/generate",
        json={
            "model": get_summary_model(),
            "prompt": (
                "Summarize this into one short durable memory sentence about the user. "
                "Keep preferences, goals, or identity details that may help future chats.\n\n"
                f"{text}"
            ),
            "stream": False,
        },
        timeout=90,
    )
    if not response.ok:
        raise MemoryServiceError(
            f"Ollama summarization failed ({response.status_code}): {response.text.strip() or 'Unknown error.'}"
        )

    payload = response.json()
    summary = str(payload.get("response", "")).strip()
    if not summary:
        raise MemoryServiceError("Ollama did not return a usable memory summary.")

    return summary


def should_store_memory(msg: str) -> bool:
    triggers = [
        "i like",
        "i prefer",
        "i am learning",
        "my goal",
        "remember",
        "my name is",
    ]
    lowered = msg.lower()
    return any(trigger in lowered for trigger in triggers)


def importance_score(msg: str) -> float:
    lowered = msg.lower()
    if "remember" in lowered:
        return 0.9
    if "goal" in lowered:
        return 0.8
    if "like" in lowered or "prefer" in lowered:
        return 0.7
    return 0.5


def unique_preserving_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        lowered = value.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        result.append(value)
    return result


def clamp_text_list(values: list[str]) -> list[str]:
    cleaned = [value.strip() for value in values if isinstance(value, str) and value.strip()]
    return unique_preserving_order(cleaned)[: get_profile_max_list_items()]


def average_user_words(messages: list[tuple[str, str]]) -> float:
    user_messages = [content for role, content in messages if role == "user" and content.strip()]
    if not user_messages:
        return 0.0
    return sum(len(message.split()) for message in user_messages) / len(user_messages)


def infer_communication_style(messages: list[tuple[str, str]]) -> str:
    avg_words = average_user_words(messages)
    joined = " ".join(content.lower() for role, content in messages if role == "user")

    if "short answer" in joined or "brief" in joined or "quick" in joined:
        return "short and direct"
    if avg_words >= 28:
        return "detailed and exploratory"
    if avg_words <= 10:
        return "short and direct"
    return "balanced and practical"


def infer_expertise_level(messages: list[tuple[str, str]]) -> str:
    joined = " ".join(content.lower() for role, content in messages if role == "user")
    beginner_hints = ("beginner", "new to", "learning", "simple explanation", "explain simply")
    technical_hints = (
        "api",
        "backend",
        "postgres",
        "schema",
        "vector",
        "embedding",
        "router",
        "database",
        "python",
    )

    if any(hint in joined for hint in beginner_hints):
        return "beginner"
    technical_matches = sum(1 for hint in technical_hints if hint in joined)
    if technical_matches >= 4:
        return "technical builder"
    if technical_matches >= 2:
        return "intermediate"
    return "unknown"


def infer_preferred_tone(messages: list[tuple[str, str]]) -> str:
    joined = " ".join(content.lower() for role, content in messages if role == "user")
    if "friendly" in joined or "casual" in joined:
        return "friendly and practical"
    if "formal" in joined or "professional" in joined:
        return "professional and precise"
    if "short" in joined or "brief" in joined:
        return "concise and practical"
    if "detail" in joined or "robust" in joined:
        return "detailed and practical"
    return "clear and practical"


def split_candidate_phrases(raw_text: str) -> list[str]:
    normalized = re.split(r",| and |/|\n", raw_text)
    cleaned: list[str] = []
    for chunk in normalized:
        value = chunk.strip(" .:-")
        value = re.sub(r"^(to|that)\s+", "", value, flags=re.IGNORECASE)
        value = re.sub(r"^(i am|i'm)\s+", "", value, flags=re.IGNORECASE)
        if value:
            cleaned.append(value)
    return clamp_text_list(cleaned)


def infer_interests(messages: list[tuple[str, str]]) -> list[str]:
    interests: list[str] = []
    patterns = [
        r"\bi (?:like|love|enjoy)\s+([^.!?\n]+)",
        r"\bi am interested in\s+([^.!?\n]+)",
        r"\bi'?m interested in\s+([^.!?\n]+)",
    ]

    for _, content in messages:
        lowered = content.lower()
        for pattern in patterns:
            for match in re.findall(pattern, lowered):
                interests.extend(split_candidate_phrases(match))

    return clamp_text_list(interests)


def infer_goals(messages: list[tuple[str, str]]) -> list[str]:
    goals: list[str] = []
    patterns = [
        r"\bmy goal is to\s+([^.!?\n]+)",
        r"\bmy goal\s+is\s+([^.!?\n]+)",
        r"\bi want to\s+([^.!?\n]+)",
        r"\bi am learning\s+([^.!?\n]+)",
        r"\bi'?m learning\s+([^.!?\n]+)",
        r"\bremember\s+([^.!?\n]+)",
    ]

    for _, content in messages:
        lowered = content.lower()
        for pattern in patterns:
            for match in re.findall(pattern, lowered):
                goals.extend(split_candidate_phrases(match))

    return clamp_text_list(goals)


def infer_profile_from_messages(messages: list[tuple[str, str]]) -> StructuredUserProfile:
    return StructuredUserProfile(
        communication_style=infer_communication_style(messages),
        expertise_level=infer_expertise_level(messages),
        preferred_tone=infer_preferred_tone(messages),
        interests=infer_interests(messages),
        goals=infer_goals(messages),
    )


def extract_json_object(raw_text: str) -> dict:
    candidates = [raw_text.strip()]
    match = re.search(r"\{.*\}", raw_text, re.DOTALL)
    if match:
        candidates.append(match.group(0))

    for candidate in candidates:
        if not candidate:
            continue
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload

    raise MemoryServiceError("Profile extraction did not return valid JSON.")


def parse_text_list(value: object) -> list[str]:
    if isinstance(value, list):
        return clamp_text_list([str(item) for item in value])
    if isinstance(value, str):
        return split_candidate_phrases(value)
    return []


def compose_profile_summary(profile: StructuredUserProfile) -> str:
    parts = []

    if profile.communication_style and profile.communication_style != "unknown":
        parts.append(f"Communication style: {profile.communication_style}")
    if profile.expertise_level and profile.expertise_level != "unknown":
        parts.append(f"Expertise level: {profile.expertise_level}")
    if profile.preferred_tone:
        parts.append(f"Preferred tone: {profile.preferred_tone}")
    if profile.interests:
        parts.append("Interests: " + ", ".join(profile.interests))
    if profile.goals:
        parts.append("Goals: " + ", ".join(profile.goals))

    return " | ".join(parts) if parts else "No profile yet."


def merge_profile(primary: StructuredUserProfile, fallback: StructuredUserProfile | None = None) -> StructuredUserProfile:
    fallback = fallback or StructuredUserProfile()

    merged = StructuredUserProfile(
        communication_style=primary.communication_style if primary.communication_style != "unknown" else fallback.communication_style,
        expertise_level=primary.expertise_level if primary.expertise_level != "unknown" else fallback.expertise_level,
        preferred_tone=primary.preferred_tone if primary.preferred_tone != "clear and practical" else fallback.preferred_tone,
        interests=clamp_text_list(primary.interests or fallback.interests),
        goals=clamp_text_list(primary.goals or fallback.goals),
        summary=primary.summary if primary.summary != "No profile yet." else fallback.summary,
        last_updated=primary.last_updated or fallback.last_updated,
    )

    merged.summary = compose_profile_summary(merged)
    return merged


def fetch_profile_source_messages(user_id: str) -> list[tuple[str, str]]:
    rows = db_query(
        """
        SELECT role, content
        FROM messages
        WHERE user_id = %s
        ORDER BY created_at DESC
        LIMIT %s
        """,
        (user_id, get_profile_source_message_limit()),
        fetch="all",
    )
    messages = [(str(row[0]), str(row[1])) for row in rows] if rows else []
    messages.reverse()
    return messages


def build_profile_extraction_prompt(messages: list[tuple[str, str]], current_profile: StructuredUserProfile | None) -> str:
    transcript = "\n".join(f"{role}: {content}" for role, content in messages)
    current = current_profile.summary if current_profile and current_profile.summary else "No profile yet."

    return (
        "Extract a stable user intelligence profile from the conversation.\n"
        "Return JSON only with these keys:\n"
        "communication_style, expertise_level, preferred_tone, interests, goals.\n"
        "Rules:\n"
        "- Prefer stable long-term traits over one-off requests.\n"
        "- Keep string values short.\n"
        "- interests and goals must be arrays of short strings.\n"
        "- If a value is unknown, use 'unknown' for strings and [] for arrays.\n\n"
        f"Current profile summary:\n{current}\n\n"
        f"Messages:\n{transcript}"
    )


def call_gemini_profile_extraction(prompt: str) -> StructuredUserProfile:
    _, api_key = get_first_env_value(GEMINI_API_KEY_NAMES)
    if not api_key:
        raise MemoryServiceError("No Gemini API key is configured for user profile extraction.")

    response = requests.post(
        GEMINI_GENERATE_URL_TEMPLATE.format(model=get_profile_gemini_model()),
        headers={"x-goog-api-key": api_key},
        json={
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json"},
        },
        timeout=90,
    )
    if not response.ok:
        raise MemoryServiceError(
            f"Gemini profile extraction failed ({response.status_code}): {response.text.strip() or 'Unknown error.'}"
        )

    payload = response.json()
    texts: list[str] = []
    for candidate in payload.get("candidates", []):
        content = candidate.get("content", {})
        for part in content.get("parts", []):
            text = part.get("text")
            if text:
                texts.append(text)

    if not texts:
        raise MemoryServiceError("Gemini did not return a structured user profile.")

    return profile_from_payload(extract_json_object("\n".join(texts)))


def call_ollama_profile_extraction(prompt: str) -> StructuredUserProfile:
    response = requests.post(
        f"{get_ollama_url()}/api/generate",
        json={
            "model": get_profile_model(),
            "prompt": prompt,
            "stream": False,
            "format": "json",
        },
        timeout=90,
    )
    if not response.ok:
        raise MemoryServiceError(
            f"Ollama profile extraction failed ({response.status_code}): {response.text.strip() or 'Unknown error.'}"
        )

    payload = response.json()
    raw_response = str(payload.get("response", "")).strip()
    if not raw_response:
        raise MemoryServiceError("Ollama did not return a structured user profile.")

    return profile_from_payload(extract_json_object(raw_response))


def profile_from_payload(payload: dict) -> StructuredUserProfile:
    return StructuredUserProfile(
        communication_style=str(payload.get("communication_style", "unknown")).strip() or "unknown",
        expertise_level=str(payload.get("expertise_level", "unknown")).strip() or "unknown",
        preferred_tone=str(payload.get("preferred_tone", "clear and practical")).strip() or "clear and practical",
        interests=parse_text_list(payload.get("interests")),
        goals=parse_text_list(payload.get("goals")),
    )


def get_user_profile(user_id: str) -> StructuredUserProfile | None:
    row = db_query(
        """
        SELECT summary, communication_style, expertise_level, preferred_tone, interests, goals, last_updated
        FROM user_profiles
        WHERE user_id = %s
        """,
        (user_id,),
        fetch="one",
    )
    if not row:
        return None

    return StructuredUserProfile(
        summary=str(row[0] or "No profile yet."),
        communication_style=str(row[1] or "unknown"),
        expertise_level=str(row[2] or "unknown"),
        preferred_tone=str(row[3] or "clear and practical"),
        interests=clamp_text_list(list(row[4] or [])),
        goals=clamp_text_list(list(row[5] or [])),
        last_updated=str(row[6]) if row[6] is not None else None,
    )


def should_refresh_user_profile(user_id: str) -> bool:
    existing_profile = get_user_profile(user_id)

    if not existing_profile:
        count_row = db_query(
            "SELECT COUNT(*) FROM messages WHERE user_id = %s AND role = 'user'",
            (user_id,),
            fetch="one",
        )
        return int(count_row[0] or 0) >= 1 if count_row else False

    count_row = db_query(
        """
        SELECT COUNT(*)
        FROM messages
        WHERE user_id = %s
          AND role = 'user'
          AND created_at > COALESCE((SELECT last_updated FROM user_profiles WHERE user_id = %s), TO_TIMESTAMP(0))
        """,
        (user_id, user_id),
        fetch="one",
    )
    return int(count_row[0] or 0) >= get_profile_refresh_every() if count_row else False


def upsert_user_profile(user_id: str, profile: StructuredUserProfile) -> None:
    summary = compose_profile_summary(profile)
    profile.summary = summary

    embedding_literal: str | None = None
    try:
        embedding_literal = vector_literal(get_embedding(summary))
    except MemoryServiceError:
        embedding_literal = None

    db_query(
        """
        INSERT INTO user_profiles (
            user_id,
            summary,
            embedding,
            communication_style,
            expertise_level,
            preferred_tone,
            interests,
            goals,
            last_updated
        )
        VALUES (%s, %s, %s::vector, %s, %s, %s, %s, %s, NOW())
        ON CONFLICT (user_id)
        DO UPDATE SET
            summary = EXCLUDED.summary,
            embedding = COALESCE(EXCLUDED.embedding, user_profiles.embedding),
            communication_style = EXCLUDED.communication_style,
            expertise_level = EXCLUDED.expertise_level,
            preferred_tone = EXCLUDED.preferred_tone,
            interests = EXCLUDED.interests,
            goals = EXCLUDED.goals,
            last_updated = NOW()
        """,
        (
            user_id,
            summary,
            embedding_literal,
            profile.communication_style,
            profile.expertise_level,
            profile.preferred_tone,
            profile.interests,
            profile.goals,
        ),
        fetch="none",
    )


def update_user_profile(user_id: str) -> bool:
    messages = fetch_profile_source_messages(user_id)
    if not messages:
        return False

    existing_profile = get_user_profile(user_id)
    heuristic_profile = infer_profile_from_messages(messages)
    prompt = build_profile_extraction_prompt(messages, existing_profile)

    extracted_profile: StructuredUserProfile | None = None
    errors: list[str] = []

    for extractor in (call_gemini_profile_extraction, call_ollama_profile_extraction):
        try:
            extracted_profile = extractor(prompt)
            break
        except MemoryServiceError as exc:
            errors.append(str(exc))

    merged_profile = merge_profile(extracted_profile or heuristic_profile, heuristic_profile)
    if existing_profile:
        merged_profile = merge_profile(merged_profile, existing_profile)

    if not merged_profile.summary and errors:
        merged_profile.summary = compose_profile_summary(heuristic_profile)

    upsert_user_profile(user_id, merged_profile)
    return True


def build_profile_behavior_instructions(profile: StructuredUserProfile) -> list[str]:
    instructions: list[str] = []
    style = profile.communication_style.lower()
    expertise = profile.expertise_level.lower()
    tone = profile.preferred_tone.lower()

    if "short" in style or "direct" in style or "concise" in tone:
        instructions.append("Lead with the answer and keep the response concise.")
    elif "detailed" in style or "exploratory" in style:
        instructions.append("Include a bit more explanation and context where it helps.")
    else:
        instructions.append("Keep the answer clear, practical, and easy to scan.")

    if "beginner" in expertise:
        instructions.append("Use simple explanations, define jargon briefly, and include concrete examples when useful.")
    elif "expert" in expertise or "technical" in expertise or "intermediate" in expertise:
        instructions.append("Do not over-explain basics; keep the technical depth appropriate for a builder.")

    if "friendly" in tone:
        instructions.append("Keep the tone warm and encouraging.")
    elif "professional" in tone or "precise" in tone:
        instructions.append("Keep the tone polished and precise.")

    if profile.goals:
        instructions.append("When relevant, connect the answer to the user's goals: " + ", ".join(profile.goals) + ".")
    if profile.interests:
        instructions.append("Use examples aligned with the user's interests when it helps: " + ", ".join(profile.interests) + ".")

    return instructions


def ensure_user(user_id: str, email: str | None = None) -> None:
    db_query(
        """
        INSERT INTO users (id, email)
        VALUES (%s, %s)
        ON CONFLICT (id) DO UPDATE
        SET email = COALESCE(users.email, EXCLUDED.email)
        """,
        (user_id, email),
        fetch="none",
    )


def save_message(user_id: str, role: str, content: str) -> None:
    db_query(
        """
        INSERT INTO messages (id, user_id, role, content)
        VALUES (%s, %s, %s, %s)
        """,
        (str(uuid.uuid4()), user_id, role, content),
        fetch="none",
    )


def persist_user_message(user_id: str, message: str, email: str | None = None) -> bool:
    ensure_user(user_id, email=email)
    save_message(user_id, "user", message)

    if should_store_memory(message):
        try:
            return store_memory(user_id, message)
        except MemoryServiceError:
            return False
    return False


def persist_assistant_message(user_id: str, assistant_text: str) -> None:
    ensure_user(user_id)
    save_message(user_id, "assistant", assistant_text)


def store_memory(user_id: str, message: str) -> bool:
    if not should_store_memory(message):
        return False

    summary = summarize_memory(message)
    embedding = get_embedding(summary)
    score = importance_score(message)

    db_query(
        """
        INSERT INTO memory_chunks (id, user_id, content, embedding, importance_score)
        VALUES (%s, %s, %s, %s::vector, %s)
        """,
        (str(uuid.uuid4()), user_id, summary, vector_literal(embedding), score),
        fetch="none",
    )
    return True


def get_recent_messages(user_id: str, limit: int | None = None, *, exclude_message: str | None = None) -> list[tuple[str, str]]:
    recent_limit = limit or get_recent_message_limit()
    rows = db_query(
        """
        SELECT role, content
        FROM messages
        WHERE user_id = %s
        ORDER BY created_at DESC
        LIMIT %s
        """,
        (user_id, recent_limit + 1),
        fetch="all",
    )
    recent = [(str(row[0]), str(row[1])) for row in rows] if rows else []
    recent.reverse()

    if exclude_message and recent and recent[-1][0] == "user" and recent[-1][1] == exclude_message:
        recent = recent[:-1]

    return recent[-recent_limit:]


def get_relevant_memories(user_id: str, message: str) -> list[str]:
    exists = db_query(
        "SELECT 1 FROM memory_chunks WHERE user_id = %s LIMIT 1",
        (user_id,),
        fetch="one",
    )
    if not exists:
        return []

    try:
        query_embedding = get_embedding(message)
    except MemoryServiceError:
        return []

    rows = db_query(
        """
        SELECT content
        FROM memory_chunks
        WHERE user_id = %s
        ORDER BY embedding <=> %s::vector, importance_score DESC, created_at DESC
        LIMIT %s
        """,
        (user_id, vector_literal(query_embedding), get_memory_limit()),
        fetch="all",
    )
    return [str(row[0]) for row in rows] if rows else []


def get_hybrid_memory(user_id: str, message: str) -> tuple[list[str], list[tuple[str, str]]]:
    memories = get_relevant_memories(user_id, message)
    recent = get_recent_messages(user_id, exclude_message=message)
    return memories, recent


def get_user_profile_summary(user_id: str) -> str:
    profile = get_user_profile(user_id)
    if not profile:
        return "No profile yet."
    return profile.summary or "No profile yet."


def maybe_refresh_user_profile(user_id: str) -> bool:
    if not should_refresh_user_profile(user_id):
        return False
    return update_user_profile(user_id)


def build_prompt(user_id: str, user_message: str) -> HybridMemoryContext:
    memories, recent = get_hybrid_memory(user_id, user_message)
    profile = get_user_profile(user_id) or StructuredUserProfile()
    profile.summary = compose_profile_summary(profile)
    behavior_instructions = build_profile_behavior_instructions(profile)

    mem_text = "\n".join(f"- {memory}" for memory in memories) if memories else "- No relevant memory yet."
    chat_text = "\n".join(f"{role}: {content}" for role, content in recent) if recent else "No recent chat yet."
    instruction_text = (
        "\n".join(f"- {instruction}" for instruction in behavior_instructions)
        if behavior_instructions
        else "- Keep the answer clear and practical."
    )

    prompt = (
        "[User Intelligence Profile]\n"
        f"Communication style: {profile.communication_style}\n"
        f"Expertise level: {profile.expertise_level}\n"
        f"Preferred tone: {profile.preferred_tone}\n"
        f"Interests: {', '.join(profile.interests) if profile.interests else 'unknown'}\n"
        f"Goals: {', '.join(profile.goals) if profile.goals else 'unknown'}\n"
        f"Summary: {profile.summary}\n\n"
        "[Behavior Instructions]\n"
        f"{instruction_text}\n\n"
        "[Relevant Memory]\n"
        f"{mem_text}\n\n"
        "[Recent Chat]\n"
        f"{chat_text}\n\n"
        "[User Message]\n"
        f"{user_message}"
    )

    return HybridMemoryContext(
        user_id=user_id,
        prompt=prompt,
        profile_summary=profile.summary,
        relevant_memories=memories,
        recent_messages=recent,
        user_profile=profile,
        behavior_instructions=behavior_instructions,
    )


def prepare_text_memory_context(user_id: str, user_message: str, email: str | None = None) -> HybridMemoryContext:
    normalized_user_id = normalize_user_id(user_id)
    memory_stored = persist_user_message(normalized_user_id, user_message, email=email)
    profile_updated = maybe_refresh_user_profile(normalized_user_id)
    context = build_prompt(normalized_user_id, user_message)
    context.memory_stored = memory_stored
    context.profile_updated = profile_updated
    return context


def reset_short_term_messages(user_id: str) -> None:
    normalized_user_id = normalize_user_id(user_id)
    db_query(
        "DELETE FROM messages WHERE user_id = %s",
        (normalized_user_id,),
        fetch="none",
    )
