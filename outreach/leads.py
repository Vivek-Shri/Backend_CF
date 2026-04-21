# outreach/leads.py
import os
import json
import csv
from .config import RUN_BOOKMARK_FILE

def load_leads_from_env():
    raw = os.environ.get("COMPANIES_DATA", "[]")
    try:
        leads = json.loads(raw)
        if isinstance(leads, list): return leads
    except Exception: pass
    return []

def load_leads_from_path(path: str):
    leads = []
    if not os.path.exists(path): return leads
    try:
        if path.lower().endswith(".json"):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, list) else []
        else:
            with open(path, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    leads.append(row)
    except Exception as e:
        print(f" [Leads] Error loading {path}: {e}")
    return leads

def save_bookmark(processed_indices: set[int]):
    try:
        os.makedirs(os.path.dirname(RUN_BOOKMARK_FILE), exist_ok=True)
        with open(RUN_BOOKMARK_FILE, "w", encoding="utf-8") as f:
            json.dump(list(processed_indices), f)
    except Exception: pass

def load_bookmark() -> set[int]:
    if not os.path.exists(RUN_BOOKMARK_FILE): return set()
    try:
        with open(RUN_BOOKMARK_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    except Exception: return set()
