import re
import os
import json
import asyncio
from datetime import datetime

def yn(flag: bool) -> str:
    return "Yes" if flag else "No"

def format_duration(seconds: float) -> str:
    try:
        total = max(0, int(float(seconds or 0)))
    except (ValueError, TypeError):
        total = 0
    hours, rem = divmod(total, 3600)
    mins, secs = divmod(rem, 60)
    if hours > 0:
        return f"{hours:02d}:{mins:02d}:{secs:02d}"
    return f"{mins:02d}:{secs:02d}"

def mask_secret(value: str | None, head: int = 6, tail: int = 6) -> str:
    text = str(value or "")
    if not text:
        return ""
    if len(text) <= (head + tail + 3):
        return text
    return f"{text[:head]}...{text[-tail:]}({len(text)})"

def env_int(name: str, default: int) -> int:
    try:
        return int(str(os.environ.get(name, default) or default).strip())
    except Exception:
        return int(default)

def is_low_signal_field_value(field_key: str, field_value: str, echo_re: re.Pattern) -> bool:
    key_norm = re.sub(r"[^a-z0-9]+", "", str(field_key or "").lower())
    value_norm = re.sub(r"[^a-z0-9]+", "", str(field_value or "").lower())

    if not value_norm:
        return True
    if key_norm and value_norm == key_norm:
        return True

    if echo_re.match(value_norm):
        if key_norm.endswith(value_norm) or value_norm.endswith(key_norm):
            return True

    return False

def extract_json_candidate(raw_text: str) -> str:
    if not raw_text:
        return ""
    text = raw_text.strip()
    if text.startswith("```"):
        # Strip markdown code blocks
        lines = text.splitlines()
        if len(lines) >= 2:
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            text = "\n".join(lines).strip()
    
    # Find the bracketed content
    start_idx = text.find('{')
    end_idx = text.rfind('}')
    if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
        return text[start_idx:end_idx+1]
        
    start_idx_arr = text.find('[')
    end_idx_arr = text.rfind(']')
    if start_idx_arr != -1 and end_idx_arr != -1 and end_idx_arr > start_idx_arr:
        return text[start_idx_arr:end_idx_arr+1]
        
    return text

def sanitize_pitch_text(text: str) -> str:
    if not text: return ""
    t = text.strip()
    # Remove surrounding quotes
    if len(t) >= 2 and t[0] == t[-1] and t[0] in ('"', "'"):
        t = t[1:-1].strip()
    # Clean up whitespace
    t = re.sub(r"[\r\n\t]+", " ", t)
    t = re.sub(r"\s{2,}", " ", t)
    return t.strip()
