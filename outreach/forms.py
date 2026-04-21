# outreach/forms.py
import asyncio
import re
from .browser import EXTRACT_FIELDS_JS, react_safe_fill, SHADOW_DOM_PIERCING_SCRIPT
from .utils import is_low_signal_field_value
from .config import (
    ECHO_FIELD_VALUE_RE, MY_EMAIL, MY_PHONE, MY_PHONE_INTL_E164,
    MY_FIRST_NAME, MY_LAST_NAME, MY_FULL_NAME, MY_COMPANY, MY_WEBSITE,
    MY_ADDRESS, MY_JOB_TITLE, MY_PIN_CODE, MY_COUNTRY_DIAL_CODE, MY_COUNTRY_NAME
)

async def get_all_fields(page):
    try:
        fields = await page.evaluate(EXTRACT_FIELDS_JS)
        for frame in page.frames:
            if frame == page.main_frame: continue
            try:
                iframe_fields = await frame.evaluate(EXTRACT_FIELDS_JS)
                if iframe_fields:
                    for f in iframe_fields:
                        f["iframe_url"] = frame.url
                    fields.extend(iframe_fields)
            except Exception: pass
        return fields
    except Exception: return []

async def fill_form(page, actions, persona=None):
    successful_fills = 0
    filled_data = []
    
    print(f"\n   [Step 4] Starting Form Filling ({len(actions)} actions)...")
    
    for action in actions:
        selector = action.get("sel")
        value = action.get("val")
        label = action.get("label", "unknown")
        
        if not selector or value is None:
            continue
            
        print(f"      - Filling '{label}'...")
        
        try:
            target = None
            container = page
            # 1. Try main page first
            try:
                target = await page.wait_for_selector(selector, timeout=2000)
                if target:
                    container = page
            except Exception:
                pass
            
            # 2. Try frames if main page failed
            if not target:
                for frame in page.frames:
                    try:
                        target = await frame.wait_for_selector(selector, timeout=1000)
                        if target:
                            container = frame
                            break
                    except Exception:
                        continue
            
            if target:
                # Scroll into view and focus
                try:
                    await target.scroll_into_view_if_needed()
                    await target.focus()
                    await asyncio.sleep(0.1)
                except Exception: pass
                
                await react_safe_fill(container, target, value)
                successful_fills += 1
                filled_data.append({"selector": selector, "value": value, "label": label})
                
                # Small delay for reactive UI stability
                await asyncio.sleep(0.3)
            else:
                print(f"        !! WARN: Field '{label}' not found (selector: {selector})")
        except Exception as e:
            print(f"        !! ERR: Failed '{label}': {e}")
            
    print(f"   [Step 4] Filling complete. Processed {successful_fills} fields.\n")
    return successful_fills, filled_data

async def js_fallback_fill(page, pitch, subject, persona=None):
    """Fallback fill logic using pure JS heuristics."""
    p_email = (persona or {}).get("email") or MY_EMAIL
    p_phone = (persona or {}).get("phone") or MY_PHONE
    p_fname = (persona or {}).get("first_name") or MY_FIRST_NAME
    p_lname = (persona or {}).get("last_name") or MY_LAST_NAME
    p_fullname = (persona or {}).get("full_name") or MY_FULL_NAME
    
    try:
        # Escaping for JS string template
        pitch_e = pitch.replace('`', '\\`').replace('${', '\\${')
        subject_e = subject.replace('`', '\\`').replace('${', '\\${')
        
        data = await page.evaluate(f"""() => {{
            let n = 0;
            let filled = [];
            const RF = (el, val, label) => {{
                el.value = val;
                ['input','change','blur'].forEach(evt => el.dispatchEvent(new Event(evt, {{bubbles:true}})));
                n++;
                filled.push({{ "field": label, "value": val }});
            }};
            document.querySelectorAll('input,textarea,select').forEach(el => {{
                const h = (el.name || el.id || el.placeholder || '').toLowerCase();
                if (el.type === 'email' || h.includes('email')) RF(el, '{p_email}', 'email');
                else if (h.includes('phone') || h.includes('mobile')) RF(el, '{p_phone}', 'phone');
                else if (h.includes('first') && h.includes('name')) RF(el, '{p_fname}', 'firstname');
                else if (h.includes('last') && h.includes('name')) RF(el, '{p_lname}', 'lastname');
                else if (h.includes('name')) RF(el, '{p_fullname}', 'name');
                else if (h.includes('message') || h.includes('comment') || el.tagName === 'TEXTAREA') RF(el, `{pitch_e}`, 'message');
                else if (h.includes('subject')) RF(el, `{subject_e}`, 'subject');
            }});
            return {{ "count": n, "filled": filled }};
        }}""")
        return data.get("count", 0), data.get("filled", [])
    except Exception: return 0, []

async def ensure_required_checks(page):
    """
    Ensures mandatory checkboxes (Terms, Privacy, Consent) are checked across all frames.
    Uses keyword matching and required-attribute detection.
    """
    total_checked = 0
    try:
        # Keywords that indicate a mandatory legal/consent checkbox
        CONSENT_KEYWORDS = ["terms", "privacy", "policy", "consent", "agree", "condition", "understand", "receive", "legal"]
        
        for frame in page.frames:
            try:
                n = await frame.evaluate(f"""(keywords) => {{
                    let count = 0;
                    const inputs = document.querySelectorAll('input[type="checkbox"]');
                    
                    inputs.forEach(el => {{
                        if (el.checked) return;
                        
                        const labelText = (el.labels && el.labels.length ? el.labels[0].innerText : "").toLowerCase();
                        const idOrName = (el.id + " " + el.name).toLowerCase();
                        const containerText = (el.parentElement ? el.parentElement.innerText : "").toLowerCase();
                        const fullText = labelText + " " + idOrName + " " + containerText;
                        
                        const isRequired = el.required || el.getAttribute("aria-required") === "true";
                        const matchesKeyword = keywords.some(kw => fullText.includes(kw));
                        
                        if (isRequired || matchesKeyword) {{
                            el.focus();
                            el.click(); // Primary action
                            if (!el.checked) el.checked = true; // Force if click failed
                            
                            // Trigger comprehensive events for reactive frameworks
                            ['click', 'input', 'change', 'blur'].forEach(evt => {{
                                el.dispatchEvent(new Event(evt, {{ bubbles: true, cancelable: true }}));
                            }});
                            count++;
                        }}
                    }});
                    return count;
                }}""", CONSENT_KEYWORDS)
                total_checked += (n or 0)
            except Exception:
                continue
                
        if total_checked > 0:
            print(f" [FormFill] Auto-checked {total_checked} consent/required checkbox(es) across all frames.")
            
    except Exception as e:
        print(f" [FormFill] Warning: ensure_required_checks failed: {e}")
        
    return total_checked

async def click_submit_button(page):
    """
    Try to find and click a submit button across all frames using multiple strategies.
    Uses selectors, keyword matching, and JS-level event dispatch.
    """
    selectors = [
        "button[type='submit']", "input[type='submit']",
        "button:has-text('Submit')", "button:has-text('Send')",
        "button:has-text('Contact')", "button:has-text('Request')",
        "button:has-text('Get Started')", "button:has-text('Inquiry')",
        ".wpforms-submit", ".gform_button", ".submit-button", ".btn-submit",
        "[role='button']:has-text('Submit')", "[role='button']:has-text('Send')"
    ]
    
    # Strategy 1: Standard Playwright interaction across all frames
    for frame in page.frames:
        for sel in selectors:
            try:
                btn = frame.locator(sel).first
                if await btn.is_visible() and await btn.is_enabled():
                    print(f" [FormSubmit] Clicking submit button via selector '{sel}' in frame: {frame.url}")
                    await btn.click(timeout=5000)
                    return True
            except Exception:
                continue

    # Strategy 2: Heuristic JS-level search and click across all frames
    for frame in page.frames:
        try:
            clicked = await frame.evaluate("""() => {
                const keywords = ['submit', 'send', 'contact', 'request', 'inquiry', 'message', 'start'];
                const btns = Array.from(document.querySelectorAll('button, input[type="submit"], input[type="button"], [role="button"]'));
                
                for (const b of btns) {
                    const txt = (b.innerText || b.value || b.textContent || '').toLowerCase();
                    const idOrClass = (b.id + " " + b.className).toLowerCase();
                    const fullText = txt + " " + idOrClass;
                    
                    if (keywords.some(kw => fullText.includes(kw))) {
                        b.focus();
                        b.click();
                        // Trigger additional events for reactive forms
                        ['mousedown', 'mouseup', 'pointerdown', 'pointerup'].forEach(evt => {
                            b.dispatchEvent(new PointerEvent(evt, { bubbles: true, cancelable: true }));
                        });
                        return true;
                    }
                }
                return false;
            }""")
            if clicked:
                print(f" [FormSubmit] Clicked submit button via JS heuristics in frame: {frame.url}")
                return True
        except Exception:
            continue

    # Strategy 3: Keyboard Fallback (Enter on last textarea/input)
    try:
        # Focus the last visible input/textarea and press Enter
        await page.evaluate("""() => {
            const inputs = Array.from(document.querySelectorAll('input:not([type="hidden"]), textarea'));
            if (inputs.length > 0) {
                const last = inputs[inputs.length - 1];
                last.focus();
            }
        }""")
        await page.keyboard.press("Enter")
        print(" [FormSubmit] Attempting submit via Keyboard 'Enter' fallback.")
        return True
    except Exception:
        pass

    print(" [FormSubmit] Warning: Could not find or click a definitive submit button.")
    return False
