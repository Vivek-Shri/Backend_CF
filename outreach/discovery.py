# outreach/discovery.py
import re
import asyncio
import time
import os
import urllib.parse as _urlparse
from .config import (
    CONTACT_DISCOVERY_MAX_SECONDS, CONTACT_DISCOVERY_NAV_TIMEOUT_MS,
    CONTACT_DISCOVERY_MAX_PATH_TRIES, CONTACT_DISCOVERY_STEP_PAUSE_MS,
    CONTACT_DISCOVERY_MIN_FIELDS, CONTACT_DISCOVERY_MAX_LINK_TRIES,
    CONTACT_DISCOVERY_KEYWORDS, CONTACT_DISCOVERY_COMMON_PATHS,
    CONTACT_KEYWORDS_PRIMARY, CONTACT_KEYWORDS_SECONDARY,
    CONTACT_PATHS_PRIMARY, CONTACT_PATHS_SECONDARY
)
from .browser import SHADOW_DOM_PIERCING_SCRIPT

_CONTACT_LINK_EXCLUDE_HINTS = (
    "mailto:", "tel:", "javascript:", "#",
    "facebook.com", "twitter.com", "linkedin.com",
    "instagram.com", "youtube.com", "whatsapp",
    ".pdf", ".jpg", ".jpeg", ".png", ".svg", ".zip", ".gif",
)

def normalize_website_url(raw_url: str) -> str:
    s = str(raw_url or "").strip().strip('"').strip("'")
    if not s: return ""
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", s):
        s = "https://" + s.lstrip("/")
    return s

def url_is_contact_like(url: str) -> bool:
    try: path = (_urlparse.urlparse(str(url or "")).path or "").lower()
    except Exception: return False
    return any(kw in path for kw in CONTACT_DISCOVERY_KEYWORDS)

def same_site_or_subdomain(candidate_url: str, root_netloc: str) -> bool:
    try: host = (_urlparse.urlparse(candidate_url).netloc or "").lower().lstrip("www.")
    except Exception: return False
    root = str(root_netloc or "").lower().lstrip("www.")
    if not host or not root: return False
    return host == root or host.endswith("." + root) or root.endswith("." + host)

async def _count_form_fields(frame_or_page) -> int:
    try:
        return await frame_or_page.evaluate("""() => {
            return document.querySelectorAll('input:not([type="hidden"]), textarea, select').length;
        }""")
    except Exception: return 0

async def has_form_signal_for_discovery(page) -> tuple[int, str]:
    """
    Scans all frames and returns (max_fields_in_one_frame, url_of_that_frame).
    Does NOT sum fields across frames; seeks the single best form context.
    """
    min_fields = int(CONTACT_DISCOVERY_MIN_FIELDS)
    best_count = 0
    best_url = page.url

    try:
        # Check main frame + all iframes
        for frame in page.frames:
            try:
                # Capture window.location.href inside the browser context to avoid race conditions
                stats = await frame.evaluate(f"""() => {{
                    {SHADOW_DOM_PIERCING_SCRIPT}
                    const controls = getAllElements().filter(el => {{
                        const tag = String(el.tagName || '').toLowerCase();
                        const type = String(el.type || '').toLowerCase();
                        if (!['input', 'textarea', 'select'].includes(tag)) return false;
                        if (tag === 'input' && ['hidden','submit','button','image','reset','search','file'].includes(type)) return false;
                        return true;
                    }});
                    return {{ 
                        total: controls.length,
                        url: window.location.href
                    }};
                }}""")
                
                count = stats.get("total", 0) if stats else 0
                frame_url = stats.get("url", frame.url) if stats else frame.url

                if count > best_count:
                    best_count = count
                    best_url = frame_url
                    
                if count > 0:
                    f_name = frame.name or frame_url[:50]
                    # print(f"   [FormCheck] Found {count} fields in frame: {f_name}")
            except Exception:
                continue

        if best_count >= min_fields:
            # We return the top-level page URL to ensure the form is loaded in its intended visual context
            # rather than the standalone iframe URL.
            return best_count, page.url
            
    except Exception as e:
        print(f"   [FormCheck] Error evaluating fields: {e}")
    
    return best_count, best_url

async def scrape_links_and_score(page, root_netloc: str, primary_kws: list[str], secondary_kws: list[str]) -> list[dict]:
    raw_links = []
    try:
        # Playwright locators pierce shadow DOM by default!
        link_locators = await page.locator("a").all()
        for loc in link_locators:
            try:
                # Extract details safely from each link
                info = await loc.evaluate("""(el) => {
                    return {
                        href: el.getAttribute("href"),
                        text: (el.innerText || el.textContent || el.getAttribute("aria-label") || el.getAttribute("title") || "").toLowerCase().trim()
                    };
                }""")
                if info and info.get("href"):
                    raw_links.append(info)
            except Exception:
                continue
    except Exception as e:
        print(f" [Discovery] Warn: Scrape failed: {e}")
        return []

    candidates = []
    seen_urls = set()

    for item in raw_links:
        href = item['href']
        # Absolute URL resolution
        abs_url = _urlparse.urljoin(page.url, href)
        if not same_site_or_subdomain(abs_url, root_netloc): continue
        if abs_url in seen_urls: continue
        if any(h in abs_url.lower() for h in _CONTACT_LINK_EXCLUDE_HINTS): continue
        
        url_path = _urlparse.urlparse(abs_url).path.lower()
        text = item['text']
        
        score = 0
        is_primary = False
        
        # Scoring Primary
        for kw in primary_kws:
            if kw in text or kw in url_path:
                score += 10
                is_primary = True
        
        # Scoring Secondary
        for kw in secondary_kws:
            if kw in text or kw in url_path:
                score += 5
        
        if score > 0:
            seen_urls.add(abs_url)
            candidates.append({
                "url": abs_url,
                "score": score,
                "is_primary": is_primary
            })
            
    sorted_candidates = sorted(candidates, key=lambda x: x['score'], reverse=True)
    if sorted_candidates:
        print(f" [Discovery] Scored {len(sorted_candidates)} candidate links:")
        for c in sorted_candidates[:8]:
            print(f"      - {c['score']:2d} | {'[Primary]' if c['is_primary'] else '         '} | {c['url']}")
    return sorted_candidates

# Global process memory for probes to enable learning across leads in a single run
DYNAMIC_PROBE_SLUGS = ["contact", "contact-us", "get-in-touch"]

async def discover_contact_url(page, input_url: str, search_enabled: bool = False) -> tuple[str, str, bool]:
    started_at = time.monotonic()
    deadline = started_at + float(CONTACT_DISCOVERY_MAX_SECONDS)
    seed_url = normalize_website_url(input_url)
    min_fields = int(CONTACT_DISCOVERY_MIN_FIELDS)
    
    try:
        parsed = _urlparse.urlparse(seed_url)
        base_url = f"{parsed.scheme or 'https'}://{parsed.netloc}"
        root_netloc = parsed.netloc
    except Exception: return seed_url, "parse-failed", False

    print(f" [Discovery] Search for Form Enabled: {search_enabled}")

    best_match = {"url": seed_url, "fields": 0, "meta": "initial", "found": False}

    async def _evaluate_and_track(meta_label):
        nonlocal best_match
        count, detected_url = await has_form_signal_for_discovery(page)
        if count > 0:
            print(f"   [FormCheck] Found {count} field(s) signal at: {detected_url}")
        if count >= min_fields:
            if count > best_match["fields"]:
                best_match = {
                    "url": detected_url,
                    "fields": count,
                    "meta": meta_label,
                    "found": True
                }
            # Early exit for "Strong" match (5+ fields)
            if count >= 6:
                return True
        return False

    # --- Mode B: DEEP (searchForForm is ON) ---
    checked_paths = set()
    if search_enabled:
        for path in DYNAMIC_PROBE_SLUGS:
            if time.monotonic() > deadline: break
            if path in checked_paths: continue
            checked_paths.add(path)
            
            target = _urlparse.urljoin(base_url, path)
            print(f" [Discovery] Proactive Probe: {target}")
            try:
                await page.goto(target, timeout=8000, wait_until="domcontentloaded")
                await asyncio.sleep(1.0) # Settle
                if await _evaluate_and_track(f"proactive-probe:{path}"):
                    print(f" [Discovery] STRONG SUCCESS: Found major form via '{path}' ({best_match['fields']} fields)")
                    return await _wrap_discovery_success(best_match["url"], best_match["meta"])
            except Exception: continue

    # --- Mode A: SHALLOW (Initial check) ---
    if not best_match["found"]:
        try:
            # If we strayed, goto seed
            if page.url == "about:blank":
                await page.goto(seed_url, timeout=8000, wait_until="domcontentloaded")
            await asyncio.sleep(1.0)
            if await _evaluate_and_track("initial-page"):
                if best_match["fields"] >= 6:
                    return await _wrap_discovery_success(best_match["url"], best_match["meta"])
        except Exception: pass

    # --- Scrape Homepage for Links ---
    # Scrape if we haven't found a strong match yet
    if best_match["fields"] < 6:
        try:
            if not same_site_or_subdomain(page.url, root_netloc):
                await page.goto(seed_url, timeout=5000, wait_until="domcontentloaded")
        except Exception: pass

        candidates = await scrape_links_and_score(page, root_netloc, CONTACT_KEYWORDS_PRIMARY, CONTACT_KEYWORDS_SECONDARY)
        
        # 1. PRIMARY SCRAPED LINKS
        primary_links = [c['url'] for c in candidates if c['is_primary']]
        for url in primary_links[:4]: 
            if time.monotonic() > deadline: break
            try:
                await page.goto(url, timeout=CONTACT_DISCOVERY_NAV_TIMEOUT_MS, wait_until="domcontentloaded")
                await asyncio.sleep(1.0)
                if await _evaluate_and_track(f"scraped-primary"):
                    if best_match["fields"] >= 6:
                        return await _wrap_discovery_success(best_match["url"], best_match["meta"])
            except Exception: continue

        # 2. PATH-GUESSING (Remaining Primary)
        for path in CONTACT_PATHS_PRIMARY:
            if time.monotonic() > deadline: break
            clean_path = path.strip("/")
            if clean_path in checked_paths: continue
            
            candidate = _urlparse.urljoin(base_url, path)
            try:
                await page.goto(candidate, timeout=CONTACT_DISCOVERY_NAV_TIMEOUT_MS, wait_until="domcontentloaded")
                await asyncio.sleep(1.0)
                if await _evaluate_and_track(f"path-primary:{path}"):
                    if best_match["fields"] >= 6:
                        return await _wrap_discovery_success(best_match["url"], best_match["meta"])
            except Exception: continue

    # Final selection if we have any match
    if best_match["found"]:
        print(f" [Discovery] SUCCESS: Selected best matching form at {best_match['url']} ({best_match['fields']} fields)")
        return await _wrap_discovery_success(best_match["url"], best_match["meta"])

    # 4. SECONDARY FALLBACK (Last effort)
    # ... Only if still nothing
    if not best_match["found"]:
        # Recalculate candidates if they were lost
        if 'candidates' not in locals():
            candidates = []
            
        secondary_links = [c['url'] for c in candidates if not c['is_primary']]
        secondary_pool = secondary_links[:CONTACT_DISCOVERY_MAX_LINK_TRIES] + CONTACT_PATHS_SECONDARY[:CONTACT_DISCOVERY_MAX_PATH_TRIES]
        for url_or_path in secondary_pool:
            if time.monotonic() > deadline: break
            target = url_or_path if url_or_path.startswith("http") else _urlparse.urljoin(base_url, url_or_path)
            try:
                await page.goto(target, timeout=CONTACT_DISCOVERY_NAV_TIMEOUT_MS, wait_until="domcontentloaded")
                await asyncio.sleep(1.0)
                if await _evaluate_and_track("secondary"):
                    # For secondary, take anything valid immediately
                    return await _wrap_discovery_success(best_match["url"], best_match["meta"])
            except Exception: continue

    print(f" [Discovery] FAILED: No contact form found.")
    return seed_url, "not-found", False

def _learn_slug(url: str):
    try:
        path = _urlparse.urlparse(url).path.strip("/")
        if not path: return
        slug = path.split("/")[-1].split(".")[0].lower() # handle .php, .html
        if len(slug) > 3:
            # Add to keyword scraper
            if slug not in CONTACT_KEYWORDS_PRIMARY:
                CONTACT_KEYWORDS_PRIMARY.append(slug)
            # Add to proactive probes (priority)
            if slug not in DYNAMIC_PROBE_SLUGS:
                print(f" [Discovery] Learning new PROBE slug: {slug}")
                DYNAMIC_PROBE_SLUGS.insert(0, slug) # Prioritize it at the front
    except Exception: pass

async def _wrap_discovery_success(url: str, meta: str) -> tuple[str, str, bool]:
    _learn_slug(url)
    return url, meta, True
