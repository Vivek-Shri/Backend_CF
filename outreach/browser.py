# outreach/browser.py
import os
import time
import contextlib
from urllib.parse import urlparse as _up
from playwright.async_api import async_playwright
from .config import (
    BANDWIDTH_SOFT_LIMIT_KB, BANDWIDTH_HARD_CAP_KB,
    MAX_MAIN_SCRIPT_REQ, MAX_MAIN_XHR_REQ,
    MAX_ALLOWED_HOST_SCRIPT_REQ, MAX_ALLOWED_HOST_XHR_REQ,
    VIEWPORT_WIDTH, VIEWPORT_HEIGHT
)

@contextlib.asynccontextmanager
async def create_playwright_context(proxy: dict = None, worker_index: int = 0):
    """
    Async context manager that yields (browser, context).
    Handles lifecycle and proxy configuration.
    """
    # Use environment variable to control headless mode
    headless_env = os.environ.get("HEADLESS_BROWSER", "true").lower()
    
    # ENFORCEMENT: Only the FIRST worker (index 0) gets a visible browser if headless is false.
    is_headless = True
    if headless_env == "false" and worker_index == 0:
        is_headless = False
    
    async with async_playwright() as p:
        # Launch browser
        launch_opts = {
            "headless": is_headless,
        }
        if proxy:
            launch_opts["proxy"] = proxy
            
        browser = await p.chromium.launch(**launch_opts)
        
        # Create context with fixed viewport and user agent
        context = await browser.new_context(
            viewport={"width": VIEWPORT_WIDTH, "height": VIEWPORT_HEIGHT},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        context.set_default_navigation_timeout(45000)
        context.set_default_timeout(45000)
        
        try:
            yield browser, context
        finally:
            await browser.close()

async def get_page_content(page) -> str:
    """
    Aggregates content from the main page and all its frames.
    """
    try:
        html = await page.content()
        for frame in page.frames:
            if frame == page.main_frame:
                continue
            try:
                furl = frame.url or ""
                if not furl.startswith("http"):
                    continue
                fhtml = await frame.content()
                html += f"\n\n<!-- Frame: {furl} -->\n{fhtml}"
            except Exception:
                pass
        return html
    except Exception:
        return ""

SHADOW_DOM_PIERCING_SCRIPT = """
    const getAllElements = (root = document) => {
        let elements = Array.from(root.querySelectorAll('*'));
        const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT, null, false);
        let node;
        while(node = walker.nextNode()) {
            if (node.shadowRoot) {
                elements = elements.concat(getAllElements(node.shadowRoot));
            }
        }
        return elements;
    };
"""

HIGHLIGHT_STYLE_JS = """
(function() {
    try {
        if (document.getElementById('outreach-highlight-style')) return;
        const style = document.createElement('style');
        style.id = 'outreach-highlight-style';
        style.innerHTML = `
            .outreach-highlight {
                outline: 4px solid #3b82f6 !important;
                outline-offset: 2px !important;
                transition: outline-width 0.2s ease-in-out !important;
                z-index: 10000 !important;
            }
            .outreach-fill-flash {
                background-color: rgba(59, 130, 246, 0.2) !important;
                animation: outreach-flash 0.5s ease-out !important;
            }
            @keyframes outreach-flash {
                0% { background-color: rgba(59, 130, 246, 0.5); }
                100% { background-color: transparent; }
            }
        `;
        (document.head || document.documentElement).appendChild(style);
    } catch(e) {}
})()
"""

REACT_FILL_JS = """
(function(el, value) {
    try {
        if (!el) return;
        
        // Inject style first
        if (!document.getElementById('outreach-highlight-style')) {
            const style = document.createElement('style');
            style.id = 'outreach-highlight-style';
            style.innerHTML = '.outreach-highlight { outline: 4px solid #3b82f6 !important; }';
            (document.head || document.documentElement).appendChild(style);
        }
        
        if (el.classList) {
            el.classList.add('outreach-highlight');
            setTimeout(() => { try { el.classList.remove('outreach-highlight'); } catch(e) {} }, 1000);
        }
        
        const tag = (el.tagName || '').toLowerCase();
        
        if (tag === 'select') {
            for (let i = 0; i < el.options.length; i++) {
                if (el.options[i].text.toLowerCase().includes(value.toLowerCase()) || el.options[i].value.toLowerCase().includes(value.toLowerCase())) {
                    el.selectedIndex = i;
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                    return;
                }
            }
            if (el.options.length > 1) el.selectedIndex = 1;
            el.dispatchEvent(new Event('change', { bubbles: true }));
            return;
        }

        // Focus and clear for clean state
        el.focus();
        
        var nativeInputValueSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value');
        var nativeTextAreaValueSetter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value');
        var setter = tag === 'textarea' ? nativeTextAreaValueSetter : nativeInputValueSetter;
        
        if (setter && setter.set) {
            setter.set.call(el, value);
        } else {
            el.value = value;
        }
        
        // Trigger all necessary events for React/Vue/Angular
        ['input', 'change', 'blur', 'keyup', 'keydown'].forEach(function(evtName) {
            var evt = new Event(evtName, { bubbles: true, cancelable: true });
            el.dispatchEvent(evt);
        });
        
        try {
            var inputEvt = new InputEvent('input', { bubbles: true, cancelable: true, data: value, inputType: 'insertText' });
            el.dispatchEvent(inputEvt);
        } catch(e) {}
        
    } catch(err) {
        console.error('ReactFill Error:', err);
    }
})
"""

EXTRACT_FIELDS_JS = r"""
() => {
    var results = [];
    var seen = new Set();

    function processElement(el, depth) {
        if (depth > 10) return;
        var tag = el.tagName ? el.tagName.toLowerCase() : '';
        if (!['input', 'textarea', 'select'].includes(tag)) return;
        var type = (el.type || '').toLowerCase();
        if (['hidden', 'submit', 'button', 'image', 'reset', 'file', 'search', 'password'].includes(type)) return;

        var id = el.id || '';
        var name = el.name || '';

        var nm = (el.name || el.id || el.placeholder || '').toLowerCase();
        if (/\bsearch\b|sf_s|zip.?code|keyword|flexdata/.test(nm)) return;
        if (/(wpcf7_ak_hp|honeypot|honey[_-]?pot|ak_hp|bot.?trap|leave.?blank|do.?not.?fill|nospam)/.test(nm)) return;
        if (id.toLowerCase().includes('search')) return;
        if (name.toLowerCase().includes('search') || name.toLowerCase().includes('sf_s')) return;

        var inNav = false;
        var p = el.parentElement;
        for (var d = 0; d < 8 && p; d++) {
            var ptag = (p.tagName || '').toLowerCase();
            var pcls = (p.className || '').toLowerCase();
            var pid = (p.id || '').toLowerCase();
            if (ptag === 'nav' || ((pcls.includes('search') || pid.includes('search')) && !pcls.includes('form')) || pcls.includes('nav')) {
                inNav = true;
                break;
            }
            p = p.parentElement;
        }
        if (inNav) return;
        if (el.name && (el.name.includes('g-recaptcha') || el.name.includes('h-captcha'))) return;

        var rect = el.getBoundingClientRect();
        var visible = true;
        try {
            var cs = window.getComputedStyle(el);
            visible = rect.width > 0 && rect.height > 0 && cs.display !== 'none' && cs.visibility !== 'hidden' && cs.opacity !== '0';
        } catch (e) {
            visible = rect.width > 0 && rect.height > 0;
        }
        if (depth === 0 && !visible) return;
        if (!visible) return;

        var key = tag + '|' + id + '|' + name + '|' + Math.round(rect.y);
        if (seen.has(key)) return;
        seen.add(key);

        var lbl = el.getAttribute('aria-label') || el.placeholder || '';
        if (!lbl && id) {
            var labelEl = document.querySelector('label[for="' + id + '"]');
            if (labelEl) lbl = labelEl.innerText.trim();
        }
        if (!lbl) {
            var parent = el.parentElement;
            for (var i = 0; i < 4 && parent; i++) {
                var prev = el.previousElementSibling;
                if (prev && ['LABEL','SPAN','P','DIV'].includes(prev.tagName || '')) {
                    var t = prev.innerText ? prev.innerText.trim() : '';
                    if (t && t.length < 60) { lbl = t; break; }
                }
                parent = parent.parentElement;
            }
        }

        var sel = '';
        if (id) {
            sel = '#' + CSS.escape(id);
        } else if (name) {
            sel = tag + '[name="' + name + '"]';
        } else {
            sel = tag + ':nth-of-type(' + (results.length + 1) + ')';
        }

        var required = false;
        try {
            required = !!el.required || el.matches(':required') || String(el.getAttribute('aria-required') || '').toLowerCase() === 'true';
        } catch (e) {
            required = !!el.required;
        }

        var opts = [];
        if (tag === 'select') {
            Array.from(el.options || []).slice(0, 8).forEach(function(o) {
                var t = o.text.trim();
                if (t && !/^(--|choose|select|please)/i.test(t)) opts.push(t);
            });
        }

        results.push({
            sel: sel,
            label: lbl.replace(/[*\n]/g, '').trim().slice(0, 40),
            tag: tag,
            type: type,
            name: name.slice(0, 30),
            id: id.slice(0, 30),
            required: required,
            visible: visible,
            options: opts,
            y: Math.round(rect.y)
        });
    }

    document.querySelectorAll('input, textarea, select').forEach(function(el) { processElement(el, 0); });

    function walkShadow(root, depth) {
        if (depth > 3) return;
        root.querySelectorAll('*').forEach(function(el) {
            if (el.shadowRoot) {
                el.shadowRoot.querySelectorAll('input, textarea, select').forEach(function(f) {
                    processElement(f, depth + 1);
                });
                walkShadow(el.shadowRoot, depth + 1);
            }
        });
    }
    walkShadow(document, 0);

    results.sort(function(a, b) { return a.y - b.y; });
    return results;
}
"""

_ALLOWED_JS_HOSTS = {
    "hcaptcha.com","newassets.hcaptcha.com","api.hcaptcha.com",
    "imgs.hcaptcha.com","assets.hcaptcha.com",
    "www.google.com","recaptcha.net","www.gstatic.com",
    "challenges.cloudflare.com","turnstile.cloudflare.com",
    "js.hsforms.net","forms.hsforms.com",
    "embed.typeform.com","typeform.com",
    "jotform.com","cdn.jotfor.ms",
    "wufoo.com","formstack.com","cognitoforms.com","123formbuilder.com",
    "ajax.googleapis.com","cdn.jsdelivr.net","cdnjs.cloudflare.com",
    "unpkg.com","code.jquery.com",
}
_BLOCKED_JS_HOSTS = {
    "googletagmanager.com","google-analytics.com","hotjar.com","clarity.ms",
    "mixpanel.com","segment.com","heap.io","fullstory.com","logrocket.com",
    "mouseflow.com","crazyegg.com","quantserve.com","scorecardresearch.com",
    "facebook.net","connect.facebook.net","bat.bing.com","adnxs.com",
    "outbrain.com","taboola.com","platform.twitter.com","tawk.to",
    "widget.intercom.io","js.driftt.com","cdn.livechatinc.com",
    "widget.freshworks.com","widget.tidio.com","cdn.crisp.chat",
    "js.hs-scripts.com","js.hs-analytics.net","player.vimeo.com",
    "onesignal.com","cdn.optimizely.com","browser.sentry-cdn.com",
}
_BLOCKED_XHR_PATHS = {
    "/analytics","/track","/pixel","/beacon","/collect",
    "/metric","/telemetry","/gtm","/ga.","/fbq",
}
_FORMISH_URL_HINTS = {
    "contact", "form", "inquiry", "inquire", "enquiry", "enquire",
    "submit", "lead", "quote", "callback", "appointment",
    "recaptcha", "hcaptcha", "turnstile", "captcha",
    "hsforms", "hubspot", "typeform", "jotform", "wufoo",
    "formstack", "cognitoforms", "123formbuilder",
}

def make_route_handler(main_host: str, bw: dict):
    from urllib.parse import urlparse as _up

    def _host_matches(host: str, domains: set[str]) -> bool:
        if not host: return False
        return any(host == d or host.endswith("." + d) for d in domains)

    def _is_formish_url(url_low: str) -> bool:
        return any(h in url_low for h in _FORMISH_URL_HINTS)

    async def _handler(route, request):
        req_url = request.url
        rtype = request.resource_type
        url_low = req_url.lower()

        try: req_host = _up(req_url).hostname or ""
        except Exception: req_host = ""

        is_main = (req_host == main_host or req_host.endswith("." + main_host))
        is_allowed_host = _host_matches(req_host, _ALLOWED_JS_HOSTS)
        is_blocked_host = _host_matches(req_host, _BLOCKED_JS_HOSTS)
        is_formish = _is_formish_url(url_low)

        used_bytes = int(bw.get("bytes", 0) or 0)
        soft_reached = used_bytes >= (BANDWIDTH_SOFT_LIMIT_KB * 1024)
        hard_reached = used_bytes >= (BANDWIDTH_HARD_CAP_KB * 1024)

        main_script_cap = MAX_MAIN_SCRIPT_REQ
        allowed_script_cap = MAX_ALLOWED_HOST_SCRIPT_REQ
        main_xhr_cap = MAX_MAIN_XHR_REQ
        allowed_xhr_cap = MAX_ALLOWED_HOST_XHR_REQ

        if soft_reached:
            main_script_cap = max(2, main_script_cap // 2)
            allowed_script_cap = max(1, allowed_script_cap // 2)
            main_xhr_cap = max(2, main_xhr_cap // 2)
            allowed_xhr_cap = max(1, allowed_xhr_cap // 2)
        if hard_reached:
            main_script_cap = max(1, main_script_cap // 2)
            allowed_script_cap = max(1, allowed_script_cap // 2)
            main_xhr_cap = max(1, main_xhr_cap // 2)
            allowed_xhr_cap = max(1, allowed_xhr_cap // 2)

        reason = None
        if rtype == "document":
            if not is_main and not is_allowed_host and not is_formish:
                reason = f"iframe-doc:{req_host or 'unknown'}"
        elif rtype in {"image", "media", "font", "stylesheet"}:
            reason = f"{rtype}-blocked"
        elif rtype in {"manifest", "eventsource", "websocket", "ping"} and not is_formish:
            reason = f"nonessential:{rtype}"
        elif is_blocked_host and rtype in {"script", "xhr", "fetch"}:
            reason = f"tracker:{rtype}:{req_host}"
        elif rtype in {"xhr", "fetch"} and any(kw in url_low for kw in _BLOCKED_XHR_PATHS):
            reason = "tracker-xhr"
        elif rtype == "script":
            if not (is_main or is_allowed_host):
                reason = f"3p-script:{req_host or 'unknown'}"
            else:
                counter_key = "main_scripts" if is_main else "allowed_scripts"
                cap = main_script_cap if is_main else allowed_script_cap
                current = int(bw.get(counter_key, 0) or 0)
                if current >= cap and not is_formish:
                    reason = f"script-cap:{counter_key}:{current}/{cap}"
                else:
                    bw[counter_key] = current + 1
        elif rtype in {"xhr", "fetch"}:
            if not (is_main or is_allowed_host):
                reason = f"3p-{rtype}:{req_host or 'unknown'}"
            else:
                counter_key = "main_xhr" if is_main else "allowed_xhr"
                cap = main_xhr_cap if is_main else allowed_xhr_cap
                current = int(bw.get(counter_key, 0) or 0)
                if current >= cap and not is_formish:
                    reason = f"{rtype}-cap:{counter_key}:{current}/{cap}"
                else:
                    bw[counter_key] = current + 1
        elif hard_reached:
            if rtype in {"script", "xhr", "fetch", "stylesheet", "other"} and not (is_main or is_allowed_host or is_formish):
                reason = f"bw-hard:{rtype}:{req_host or 'unknown'}"
        elif soft_reached:
            if rtype == "script" and not (is_main or is_allowed_host or is_formish):
                reason = f"bw-soft:script:{req_host or 'unknown'}"

        if reason:
            bw["blocked"] += 1
            await route.abort()
            return

        bw["allowed"] += 1
        await route.continue_()

    return _handler

def make_response_counter(bw: dict):
    def _on_response(response):
        try:
            status = int(getattr(response, "status", 0) or 0)
            if status in {204, 304}: return

            try:
                rtype = str(getattr(response.request, "resource_type", "") or "").lower()
                method = str(getattr(response.request, "method", "") or "").upper()
                url = str(getattr(response, "url", "") or "")
                recent = bw.setdefault("recent_responses", [])
                recent.append({
                    "ts": float(time.perf_counter()),
                    "url": url[:360],
                    "status": status,
                    "rtype": rtype,
                    "method": method,
                })
                if len(recent) > 180: del recent[:-180]
            except Exception: pass

            cl = response.headers.get("content-length")
            if cl:
                size = int(cl)
                if size > 0: bw["bytes"] += size
            else:
                rtype = str(getattr(response.request, "resource_type", "") or "").lower()
                if rtype == "script": bw["bytes"] += 2_500
                elif rtype == "stylesheet": bw["bytes"] += 1_000
                elif rtype == "image": bw["bytes"] += 3_000
                elif rtype in {"xhr", "fetch"}: bw["bytes"] += 1_500
                elif rtype == "document": bw["bytes"] += 20_000
                else: bw["bytes"] += 500
        except Exception: pass
    return _on_response

async def react_safe_fill(page, element_handle, value: str):
    """Fills an input/textarea element in a way that triggers React/Vue state updates."""
    try:
        if not element_handle: return
        # Apply highlighting before filling
        await element_handle.evaluate(REACT_FILL_JS, value)
    except Exception as e:
        print(f" [ReactFill] Warn: {e}")
        try: await element_handle.fill(value)
        except Exception: pass

async def highlight_detected_fields(page, fields):
    """Highlights all identified fields in the browser."""
    try:
        await page.evaluate(HIGHLIGHT_STYLE_JS)
        # Using a proper anonymous function string for page.evaluate
        for field in fields:
            sel = field.get("sel")
            if not sel: continue
            try:
                # FIX: Correctly pass sel as an argument to the evaluated script
                await page.evaluate("(sel) => { const el = document.querySelector(sel); if(el) el.classList.add('outreach-highlight'); }", sel)
            except Exception: pass
    except Exception: pass
