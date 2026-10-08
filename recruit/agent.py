"""Calls OpenAI (Responses API) and returns Nechama's structured reply."""
import json
import logging

import requests
from django.conf import settings

from .prompts import REPLY_SCHEMA, SYSTEM_PROMPT

log = logging.getLogger(__name__)
OPENAI_URL = "https://api.openai.com/v1/responses"


class AgentError(Exception):
    pass


def _extract_text(data):
    if isinstance(data.get("output_text"), str) and data["output_text"]:
        return data["output_text"]
    chunks = []
    for item in data.get("output") or []:
        if item.get("type") != "message":
            continue
        for part in item.get("content") or []:
            if part.get("type") == "output_text":
                chunks.append(part.get("text", ""))
            elif part.get("type") == "refusal":
                raise AgentError("model refused: " + part.get("refusal", ""))
    return "".join(chunks)


def ask_nechama(user_input, extra_instructions=""):
    """Send the assembled conversation to the model; return the parsed dict."""
    if not settings.OPENAI_API_KEY:
        raise AgentError("OPENAI_API_KEY is not set")
    instructions = SYSTEM_PROMPT
    if extra_instructions.strip():
        instructions += "\n\n## הנחיות נוספות מהצוות\n" + extra_instructions.strip()
    payload = {
        "model": settings.OPENAI_MODEL,
        "instructions": instructions,
        "input": user_input,
        "store": False,
        "max_output_tokens": 3000,
        "text": {
            "format": {
                "type": "json_schema",
                "name": "nechama_reply",
                "strict": True,
                "schema": REPLY_SCHEMA,
            }
        },
    }
    if settings.OPENAI_REASONING_EFFORT:
        payload["reasoning"] = {"effort": settings.OPENAI_REASONING_EFFORT}
    try:
        resp = requests.post(
            OPENAI_URL,
            headers={"Authorization": f"Bearer {settings.OPENAI_API_KEY}"},
            json=payload,
            timeout=90,
        )
    except requests.RequestException as exc:
        raise AgentError(f"OpenAI request failed: {exc}") from exc
    if resp.status_code >= 400:
        raise AgentError(f"OpenAI HTTP {resp.status_code}: {resp.text[:500]}")
    data = resp.json()
    if data.get("status") not in (None, "completed"):
        raise AgentError(f"OpenAI response status {data.get('status')}: {data.get('incomplete_details')}")
    text = _extract_text(data)
    try:
        result = json.loads(text)
    except (TypeError, ValueError) as exc:
        raise AgentError(f"Could not parse model JSON: {text[:300]}") from exc
    missing = [k for k in REPLY_SCHEMA["required"] if k not in result]
    if missing:
        raise AgentError(f"Model JSON missing keys: {missing}")
    if not str(result.get("reply", "")).strip():
        raise AgentError("Model returned an empty reply")
    return result
