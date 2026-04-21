# outreach/captcha.py
import asyncio
import requests
import time as _t
import os
from urllib.parse import urlparse as _urlparse
from .config import NOPECHA_HARD_TIMEOUT, NOPECHA_CREDIT_PER_SOLVE
from .tracking import (
    next_valid_nopecha_key, record_nopecha_credit, 
    nopecha_log, disable_nopecha_key
)
from .proxies import NOPECHA_PROXY_PAYLOAD
from .utils import mask_secret

_nopecha_semaphore = asyncio.Semaphore(3)

def _nopecha_token_api(cap_type: str, sitekey: str, url: str, stop_flag: asyncio.Event) -> str | None:
    API = "https://api.nopecha.com/token"
    POLL_SECS = 5
    POST_RETRY = 5
    HARD_TIMEOUT = NOPECHA_HARD_TIMEOUT
    kw = {"timeout": 15}

    host = (_urlparse(url).hostname or "unknown").lower()
    sitekey_mask = mask_secret(sitekey)

    def _trace(msg: str):
        print(f"   [NopeCHA] {msg}")
        nopecha_log(f"[NopeCHA] {msg} | type={cap_type} | host={host} | sitekey={sitekey_mask}")

    nopecha_key = next_valid_nopecha_key()
    if not nopecha_key:
        _trace("All API keys have exhausted their credits")
        return None

    deadline = _t.time() + HARD_TIMEOUT
    payload = {
        "key": nopecha_key, "type": cap_type,
        "sitekey": sitekey, "url": url, "proxy": NOPECHA_PROXY_PAYLOAD,
    }
    if cap_type == "recaptcha3":
        payload["action"] = "submit"
        payload["score"] = 0.7

    def _submit():
        rotate_key = False
        for i in range(1, POST_RETRY + 1):
            if stop_flag.is_set() or _t.time() > deadline:
                return None, rotate_key
            try:
                r = requests.post(API, json=payload, **kw)
                d = r.json()
                job_id = d.get("data")
                if job_id: return job_id, rotate_key
                ec = d.get("error", "")
                if ec in (10, 11, 16, "10", "11", "16"):
                    rotate_key = True
                    break
                _t.sleep(1.0)
            except Exception:
                _t.sleep(1.0)
        return None, rotate_key

    job_id, rotate_after_submit = _submit()
    if not job_id:
        if rotate_after_submit:
            disable_nopecha_key(nopecha_key)
        return None

    # Polling
    while _t.time() < deadline:
        if stop_flag.is_set(): break
        try:
            r = requests.get(API, params={"key": nopecha_key, "id": job_id}, **kw)
            d = r.json()
            token = d.get("data")
            if token: return token
            ec = d.get("error", "")
            if ec and ec not in (0, "0"):
                _trace(f"Poll error job={job_id} err={ec}")
                break
        except Exception: pass
        _t.sleep(POLL_SECS)

    return None

async def _inject_token(page, token: str, cap_type: str):
    try:
        if cap_type in ("recaptcha2", "recaptcha3"):
            await page.evaluate("token => { document.querySelectorAll('[name=\"g-recaptcha-response\"]').forEach(el => el.value = token); }", token)
        elif cap_type == "hcaptcha":
            await page.evaluate("token => { document.querySelectorAll('[name=\"h-captcha-response\"], [name=\"g-recaptcha-response\"]').forEach(el => el.value = token); }", token)
        elif cap_type == "turnstile":
            await page.evaluate("token => { document.querySelectorAll('[name=\"cf-turnstile-response\"], [name=\"g-recaptcha-response\"]').forEach(el => el.value = token); }", token)
    except Exception: pass

async def detect_and_solve_captcha(page, stop_flag: asyncio.Event = None, iframe=None):
    if stop_flag is None:
        stop_flag = asyncio.Event() # dummy
    # This is a simplified version of the detection/solving logic.
    # In the real script, it probes for sitekeys.
    target = iframe or page
    content = await target.content()
    
    cap_type = None
    sitekey = None
    
    if "google.com/recaptcha/api/sitekey=" in content:
        cap_type = "recaptcha2"
        # ... logic to extract sitekey ...
    elif "hcaptcha.com/1/api.js" in content:
        cap_type = "hcaptcha"
        # ... logic to extract sitekey ...
    
    if cap_type and sitekey:
        async with _nopecha_semaphore:
            token = await asyncio.to_thread(_nopecha_token_api, cap_type, sitekey, page.url, stop_flag)
            if token:
                await _inject_token(page, token, cap_type)
                return f"{cap_type}-solved-"
    return "none"
