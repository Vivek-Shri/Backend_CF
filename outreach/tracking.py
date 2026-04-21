import threading
import time
import requests
import os
from datetime import datetime
from .config import (
    COST_PER_1M_INPUT, COST_PER_1M_OUTPUT, TOKEN_LOG_FILE, 
    NOPECHA_API_KEYS, NOPECHA_DEBUG_LOG_FILE
)

class TokenTracker:
    def __init__(self):
        self._lock = threading.Lock()
        self.total_input = 0
        self.total_output = 0
        self.total_calls = 0
        self.worker_totals = {}

    def record(self, company: str, call_type: str, usage, worker_index: int = -1):
        def _usage_int(*names: str) -> int:
            for name in names:
                val = None
                if usage is None: continue
                try: val = getattr(usage, name)
                except Exception: val = None
                if val is None and isinstance(usage, dict): val = usage.get(name)
                try:
                    iv = int(val)
                    if iv >= 0: return iv
                except Exception: pass
            return 0

        pt = _usage_int("prompt_tokens", "input_tokens")
        ct = _usage_int("completion_tokens", "output_tokens")
        tt = _usage_int("total_tokens")
        if tt <= 0: tt = pt + ct
        if pt == 0 and ct == 0 and tt > 0: pt = tt
        est = (pt * COST_PER_1M_INPUT + ct * COST_PER_1M_OUTPUT) / 1_000_000

        with self._lock:
            self.total_input += pt
            self.total_output += ct
            self.total_calls += 1
            if worker_index >= 0:
                w = self.worker_totals.setdefault(worker_index, {"input":0,"output":0,"calls":0})
                w["input"] += pt
                w["output"] += ct
                w["calls"] += 1
            cum_cost = (self.total_input * COST_PER_1M_INPUT + self.total_output * COST_PER_1M_OUTPUT) / 1_000_000

        print(
            f"   [Tokens] [{call_type}] {company[:20]:<20} | "
            f"in={pt:>5} out={ct:>4} cost=${est:.5f} | cum=${cum_cost:.4f}"
        )

    def get_token_columns(self) -> list:
        with self._lock:
            cum_in = self.total_input
            cum_out = self.total_output
            cum_tot = cum_in + cum_out
            cum_cost = (cum_in * COST_PER_1M_INPUT + cum_out * COST_PER_1M_OUTPUT) / 1_000_000
            calls = self.total_calls
            avg_tok = (cum_tot // calls) if calls else 0
        return [
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            calls, cum_in, cum_out, cum_tot, round(cum_cost, 6), avg_tok
        ]

    def print_summary(self):
        cols = self.get_token_columns()
        print("\n" + "=" * 60)
        print("  TOKEN USAGE SUMMARY")
        print("=" * 60)
        print(f"  Total API calls     : {cols[1]}")
        print(f"  Total input tokens  : {cols[2]:,}")
        print(f"  Total output tokens : {cols[3]:,}")
        print(f"  Total tokens        : {cols[4]:,}")
        print(f"  Estimated cost      : ${cols[5]:.4f}")
        print(f"  Avg tokens / call   : {cols[6]:,}")
        print(f"  Log saved to        : {TOKEN_LOG_FILE}")
        print("\n  Per-worker breakdown:")
        with self._lock:
            for widx in sorted(self.worker_totals):
                w = self.worker_totals[widx]
                tot = w["input"] + w["output"]
                cost = (w["input"] * COST_PER_1M_INPUT + w["output"] * COST_PER_1M_OUTPUT) / 1_000_000
                print(f"    Worker #{widx}: calls={w['calls']} tokens={tot:,} cost=${cost:.5f}")
        print("=" * 60)

token_tracker = TokenTracker()

# --- NopeCHA Credit Tracking ---
_nopecha_lock = threading.Lock()
_nopecha_idx = 0
_nopecha_credit_lock = threading.Lock()
_nopecha_credit_start = {}
_nopecha_credit_current = {}
_nopecha_run_credit_lock = threading.Lock()
_nopecha_run_credit_left = None
_nopecha_key_states = {k: True for k in NOPECHA_API_KEYS}
_nopecha_file_lock = threading.Lock()

def next_valid_nopecha_key():
    global _nopecha_idx
    with _nopecha_lock:
        active_keys = [k for k in NOPECHA_API_KEYS if _nopecha_key_states.get(k, False)]
        if not active_keys: return None
        key = active_keys[_nopecha_idx % len(active_keys)]
        _nopecha_idx += 1
        return key

def disable_nopecha_key(key):
    with _nopecha_lock:
        _nopecha_key_states[key] = False

def record_nopecha_credit(key: str, credit):
    try: c = int(float(credit))
    except Exception: return
    with _nopecha_credit_lock:
        if key not in _nopecha_credit_start:
            _nopecha_credit_start[key] = c
        _nopecha_credit_current[key] = c

def nopecha_credit_totals() -> tuple[str, str]:
    with _nopecha_credit_lock:
        if not _nopecha_credit_current: return "", ""
        used = 0
        left = 0
        for k, cur in _nopecha_credit_current.items():
            start = _nopecha_credit_start.get(k, cur)
            used += max(start - cur, 0)
            left += max(cur, 0)
    return str(used), str(left)

def peek_stable_nopecha_credit_left() -> str:
    global _nopecha_run_credit_left
    with _nopecha_run_credit_lock:
        _, left_total = nopecha_credit_totals()
        try: observed = int(float(left_total)) if left_total else None
        except Exception: observed = None
        
        if observed is not None:
            if _nopecha_run_credit_left is None:
                _nopecha_run_credit_left = observed
            else:
                _nopecha_run_credit_left = min(_nopecha_run_credit_left, observed)

        if _nopecha_run_credit_left is None: return ""
        return str(max(0, int(_nopecha_run_credit_left)))

def refresh_nopecha_credit_snapshot():
    for key in NOPECHA_API_KEYS:
        try:
            r = requests.get("https://api.nopecha.com/status", params={"key": key}, timeout=8)
            data = r.json()
            credit = data.get("credit", (data.get("data") or {}).get("credit", None))
            if credit is not None:
                record_nopecha_credit(key, credit)
        except Exception: continue
    peek_stable_nopecha_credit_left()

def nopecha_log(message: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] [T{threading.get_ident()}] {message}"
    try:
        path = os.path.join(os.getcwd(), NOPECHA_DEBUG_LOG_FILE)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with _nopecha_file_lock:
            with open(path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
    except Exception: pass
