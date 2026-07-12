from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional, Tuple, Type

from pydantic import BaseModel


def extract_designated_json_block(text: str) -> Optional[str]:
    match = re.search(r"```json\s*(\{.*?\})\s*```", text, flags=re.DOTALL | re.IGNORECASE)
    if not match:
        return None
    return match.group(1).strip()


def _extract_json_objects(text: str):
    decoder = json.JSONDecoder()
    i = 0
    length = len(text)
    while i < length:
        ch = text[i]
        if ch != "{":
            i += 1
            continue
        try:
            obj, idx = decoder.raw_decode(text[i:])
            yield obj
            i += idx
        except json.JSONDecoder.JSONDecodeError:
            i += 1


def extract_last_json(text: str) -> Optional[str]:
    last = None
    for obj in _extract_json_objects(text):
        last = obj
    if last is None:
        return None
    return json.dumps(last)


def parse_structured_output(text: str, model: Type[BaseModel]) -> Tuple[Optional[BaseModel], Optional[Dict[str, Any]], Optional[str]]:
    """
    Returns:
        validated_model_or_None, raw_payload_if_any, error_message_if_any
    """
    designated = extract_designated_json_block(text)
    if designated:
        try:
            parsed = json.loads(designated)
            return model.model_validate(parsed), parsed, None
        except Exception as exc:
            return None, {"raw": designated}, f"designated-json-parse-failed: {exc}"

    fallback = extract_last_json(text)
    if fallback:
        try:
            payload = json.loads(fallback)
            return model.model_validate(payload), payload, None
        except Exception as exc:
            return None, {"raw": fallback}, f"fallback-json-parse-failed: {exc}"

    return None, None, "no-json-found"
