import os
import sys
import re
import json
import threading
from .utils import env_int

_script_dir = os.path.dirname(os.path.abspath(__file__))

def load_local_env(env_path: str = ".env"):
    """Load KEY=VALUE pairs from a local .env file into process env (without overriding existing vars)."""
    if not os.path.exists(env_path):
        return
    try:
        with open(env_path, "r", encoding="utf-8") as fh:
            for raw_line in fh:
                line = str(raw_line or "").strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("export "):
                    line = line[7:].strip()
                if "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                if not key or key in os.environ:
                    continue
                value = value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                    value = value[1:-1]
                os.environ[key] = value
    except Exception as e:
        print(f"[Env] Warning: failed loading {env_path}: {e}")

def initialize_env():
    backend_dir = os.path.dirname(_script_dir)
    _env_candidates = [
        os.path.join(os.getcwd(), ".env.outreach"),
        os.path.join(backend_dir, ".env.outreach"),
        os.path.join(os.getcwd(), ".env"),
        os.path.join(backend_dir, ".env"),
        os.path.join(backend_dir, "..", ".env"),
    ]
    _env_seen = set()
    for _env_path in _env_candidates:
        _norm = os.path.normcase(os.path.abspath(_env_path))
        if _norm in _env_seen:
            continue
        _env_seen.add(_norm)
        load_local_env(_env_path)

# Initialize env ONCE upon import
initialize_env()

# --- API Keys ---
OPENAI_API_KEY = str(os.environ.get("OPENAI_API_KEY", "") or "").strip()
NOPECHA_API_KEYS = [
    str(os.environ.get("NOPECHA_KEY_1", "") or "").strip(),
    str(os.environ.get("NOPECHA_KEY_2", "") or "").strip(),
]

# --- Models ---
OPENAI_FORM_FILL_MODEL = str(os.environ.get("OPENAI_FORM_FILL_MODEL", "gpt-5-nano") or "gpt-5-nano").strip()

# --- Spreadsheet info ---
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID", "1H5ZyBKwKfoXledQgEDk9LvO4KDXzH3plkeamgEXhrWs")
CREDS_FILE = str(os.environ.get("CREDS_FILE", "google_credentials.json") or "google_credentials.json").strip()

# --- Identity Info ---
MY_FIRST_NAME = os.environ.get("MY_FIRST_NAME", "Uttam").strip()
MY_LAST_NAME = os.environ.get("MY_LAST_NAME", "Tiwari").strip()
MY_FULL_NAME = os.environ.get("MY_FULL_NAME", "Uttam Tiwari").strip()
MY_EMAIL = re.sub(r"\s+", "", str(os.environ.get("MY_EMAIL", "uttam.tiwari@mail.hyperstaff.co") or "").strip().lower())

MY_PHONE = os.environ.get("MY_PHONE", "+1 347-997-9083").strip()
MY_PHONE_INTL = os.environ.get("MY_PHONE_INTL", "+1 347-997-9083").strip()
MY_PHONE_INTL_E164 = "+" + re.sub(r"\D+", "", str(MY_PHONE_INTL or ""))
if MY_PHONE_INTL_E164 == "+":
    MY_PHONE_INTL_E164 = "+13479979083"

MY_PIN_CODE = os.environ.get("MY_PIN_CODE", "123456").strip()
MY_PHONE_DISPLAY = os.environ.get("MY_PHONE_DISPLAY", "+1 347-997-9083").strip()
MY_COMPANY = os.environ.get("MY_COMPANY", "HyperStaff").strip()
MY_WEBSITE = os.environ.get("MY_WEBSITE", "https://hyperstaff.co").strip()
MY_ADDRESS = os.environ.get("MY_ADDRESS", "Delhi, India").strip()
MY_JOB_TITLE = str(os.environ.get("MY_JOB_TITLE", "Founder") or "").strip()
MY_TITLE = os.environ.get("MY_TITLE", "Inquiry for {company_name}")

def derive_country_dial_code():
    raw = os.environ.get("MY_COUNTRY_DIAL_CODE", "")
    digits = re.sub(r"\D+", "", str(raw or ""))
    if not digits:
        _intl_seed = re.sub(r"\D+", "", str(MY_PHONE_INTL_E164 or ""))
        if _intl_seed.startswith("971"): digits = "971"
        elif _intl_seed.startswith("91"): digits = "91"
        elif _intl_seed.startswith("44"): digits = "44"
        elif _intl_seed.startswith("61"): digits = "61"
        elif _intl_seed.startswith("1"): digits = "1"
        else: digits = _intl_seed[:1] if _intl_seed else "1"
    return f"+{digits or '1'}"

MY_COUNTRY_DIAL_CODE = derive_country_dial_code()

def derive_country_name():
    explicit = str(os.environ.get("MY_COUNTRY_NAME", "") or "").strip()
    if explicit: return explicit
    address_upper = str(MY_ADDRESS or "").upper()
    address_map = [
        ("UNITED ARAB EMIRATES", "United Arab Emirates"), ("UAE", "United Arab Emirates"),
        ("UNITED STATES", "United States"), ("USA", "United States"),
        ("CANADA", "Canada"), ("UNITED KINGDOM", "United Kingdom"), ("UK", "United Kingdom"),
        ("AUSTRALIA", "Australia"), ("INDIA", "India"),
    ]
    for needle, resolved in address_map:
        if needle in address_upper: return resolved
    dial_dig = MY_COUNTRY_DIAL_CODE.lstrip('+')
    dial_map = {"971": "United Arab Emirates", "91": "India", "44": "United Kingdom", "61": "Australia", "1": "United States"}
    return dial_map.get(dial_dig, "India")

MY_COUNTRY_NAME = derive_country_name()

# --- Browser/Worker Limits ---
PARALLEL_COUNT = env_int("OUTREACH_PARALLEL_COUNT", 1)
VIEWPORT_WIDTH = 960
VIEWPORT_HEIGHT = 1080
USE_PROXY = str(os.environ.get("USE_PROXY", "1")).strip().lower() not in {"0", "false", "no", "off"}
ENABLE_CONTACT_DISCOVERY = str(os.environ.get("ENABLE_CONTACT_DISCOVERY", "1")).strip().lower() not in {"0", "false", "no", "off"}

BANDWIDTH_SOFT_LIMIT_KB = max(300, env_int("BANDWIDTH_SOFT_LIMIT_KB", 650))
BANDWIDTH_HARD_CAP_KB = max(BANDWIDTH_SOFT_LIMIT_KB + 20, env_int("BANDWIDTH_HARD_CAP_KB", 850))

MAX_MAIN_SCRIPT_REQ = max(2, env_int("MAX_MAIN_SCRIPT_REQ", 12))
MAX_MAIN_XHR_REQ = max(2, env_int("MAX_MAIN_XHR_REQ", 14))
MAX_ALLOWED_HOST_SCRIPT_REQ = max(1, env_int("MAX_ALLOWED_HOST_SCRIPT_REQ", 6))
MAX_ALLOWED_HOST_XHR_REQ = max(1, env_int("MAX_ALLOWED_HOST_XHR_REQ", 8))

OUTREACH_MAX_DAILY_SUBMISSIONS = env_int("OUTREACH_MAX_DAILY_SUBMISSIONS", 0)
OUTREACH_BREAK_ON_FAILURE = str(os.environ.get("OUTREACH_BREAK_ON_FAILURE", "0")).strip().lower() in {"1", "true", "yes", "on"}

# --- LLM Form Fill Budgets ---
MAX_INPUT_TOKENS = 999
MAX_OUTPUT_TOKENS = 260
MAX_OUTPUT_TOKENS_RECOVERY = 700
FORM_FILL_MAX_INPUT_TOKENS = max(900, min(2000, env_int("FORM_FILL_MAX_INPUT_TOKENS", 2000)))
FORM_FILL_INPUT_BUDGET_TARGET = max(800, FORM_FILL_MAX_INPUT_TOKENS - 120)
FORM_FILL_MAX_OUTPUT_TOKENS = max(120, min(1500, env_int("FORM_FILL_MAX_OUTPUT_TOKENS", 800)))
FORM_FILL_MAX_OUTPUT_TOKENS_RECOVERY = max(
    FORM_FILL_MAX_OUTPUT_TOKENS,
    min(1500, env_int("FORM_FILL_MAX_OUTPUT_TOKENS_RECOVERY", FORM_FILL_MAX_OUTPUT_TOKENS + 200)),
)
FORM_FILL_FIELD_CATALOG_LIMIT = max(8, min(30, env_int("FORM_FILL_FIELD_CATALOG_LIMIT", 18)))

# --- Proxies ---
PROXY_LIST = [
    ("p.webshare.io", 80, "nehybklk-rotate",  "hgu3dl519zlc", "webshare0"),
    ("p.webshare.io", 80, "nehybklk-rotate",  "hgu3dl519zlc", "webshare1"),
    ("p.webshare.io", 80, "nehybklk-rotate",  "hgu3dl519zlc", "webshare2"),
    ("p.webshare.io", 80, "nehybklk-rotate",  "hgu3dl519zlc", "webshare3"),
    ("p.webshare.io", 80, "nehybklk-rotate",  "hgu3dl519zlc", "webshare4"),
    ("p.webshare.io", 80, "nehybklk-rotate",  "hgu3dl519zlc", "webshare5"),
    ("p.webshare.io", 80, "nehybklk-rotate",  "hgu3dl519zlc", "webshare6"),
    ("p.webshare.io", 80, "nehybklk-rotate",  "hgu3dl519zlc", "webshare7"),
    ("p.webshare.io", 80, "nehybklk-rotate",  "hgu3dl519zlc", "webshare8"),
    ("p.webshare.io", 80, "nehybklk-rotate",  "hgu3dl519zlc", "webshare9"),
]

NOPECHA_PROXY_PAYLOAD = {
    "scheme": "http",
    "host": PROXY_LIST[0][0],
    "port": str(PROXY_LIST[0][1]),
    "username": PROXY_LIST[0][2],
    "password": PROXY_LIST[0][3],
}

# --- Contact Discovery ---
CONTACT_DISCOVERY_MAX_SECONDS = max(6, env_int("CONTACT_DISCOVERY_MAX_SECONDS", 35))
CONTACT_DISCOVERY_NAV_TIMEOUT_MS = max(2000, env_int("CONTACT_DISCOVERY_NAV_TIMEOUT_MS", 9000))
CONTACT_DISCOVERY_MAX_PATH_TRIES = max(2, min(30, env_int("CONTACT_DISCOVERY_MAX_PATH_TRIES", 20)))
CONTACT_DISCOVERY_MAX_LINK_TRIES = max(1, min(12, env_int("CONTACT_DISCOVERY_MAX_LINK_TRIES", 6)))
CONTACT_DISCOVERY_STEP_PAUSE_MS = max(0, min(1200, env_int("CONTACT_DISCOVERY_STEP_PAUSE_MS", 350)))
CONTACT_DISCOVERY_MIN_FIELDS = max(2, min(8, env_int("CONTACT_DISCOVERY_MIN_FIELDS", 2)))

# Tiered Keywords
CONTACT_KEYWORDS_PRIMARY = [
    "contact", "contact-us", "contact_us", "contactus",
    "get-in-touch", "reach-us", "reach_us",
    "quote", "request-quote", "request-demo", "demo",
    "schedule", "book", "appointment", "hire",
]
CONTACT_KEYWORDS_SECONDARY = [
    "getintouch", "touch", "pricing", "cost",
    "inquiry", "enquiry", "support", "help", "write-to-us", "talk-to-us",
    "connect", "reach", "message", "message-us", "send-message",
    "services", "solutions", "trial", "evaluation", "calculator",
]

# Tiered Paths
CONTACT_PATHS_PRIMARY = [
    "/contact-us", "/contact_us", "/contact", "/contactus", "/contact.php", 
    "/contact-us.php", "/contact.html", "/contact-us.html", "/contact-us/", "/contact/"
]
CONTACT_PATHS_SECONDARY = [
    "/get-in-touch", "/reach-us", "/inquiry", "/enquiry", "/support/contact",
    "/about/contact", "/pages/contact", "/help/contact", "/connect", 
    "/about-us/contact", "/reach", "/touch", "/feedback", "/message", 
    "/message-us", "/send-message", "/request-info", "/request-information"
]

CONTACT_DISCOVERY_KEYWORDS = CONTACT_KEYWORDS_PRIMARY + CONTACT_KEYWORDS_SECONDARY
CONTACT_DISCOVERY_COMMON_PATHS = CONTACT_PATHS_PRIMARY + CONTACT_PATHS_SECONDARY

# --- Constants & Regex ---
SHEET_HEADERS = [
    "Website URL", "Contact Page URL", "Contact Form Present", "Input Tokens", "Output Tokens",
    "Bandwidth Taken", "Submitted w/o Captcha", "Captcha Present", "Nopecha Credits Left",
    "Captcha Solved", "Submitted with Captcha", "Time taken", "Proxy Used", "Submission Content",
    "Response", "Reason for Failure", "Timestamp", "Submitted Overall",
]

COST_PER_1M_INPUT = 0.150
COST_PER_1M_OUTPUT = 0.600
TOKEN_LOG_FILE = "token_usage.csv"
# Use RUN_ID and STEP from env or fallback to a default
_run_id = os.environ.get("OUTREACH_RUN_ID", "default")
_step = os.environ.get("OUTREACH_STEP", "0")
RUN_BOOKMARK_FILE = os.path.join(".outreach-runs", f"resume-{_run_id}-step-{_step}.json")
NOPECHA_DEBUG_LOG_FILE = os.path.join(".outreach-runs", "nopecha_debug.txt")
NOPECHA_HARD_TIMEOUT = 300
NOPECHA_CREDIT_PER_SOLVE = max(0, int(str(os.environ.get("NOPECHA_CREDIT_PER_SOLVE", "20") or "20").strip() or "20"))

HONEYPOT_FIELD_RE = re.compile(
    r"(wpcf7_ak_hp|honeypot|honey[_-]?pot|ak_hp|bot.?trap|do.?not.?fill|leave.?blank|nospam)", re.I,
)
ECHO_FIELD_VALUE_RE = re.compile(
    r"^(fname|lname|firstname|lastname|name|email|mail|phone|phno|mobile|address|city|state|zip|pincode|postal|comment|comments|message|subject)$", re.I,
)

# Campaign steps are now fetched dynamically from the database in worker.py and engine.py
