# outreach/worker.py
import asyncio
import time
import json
import os
import random
from .config import (
    ECHO_FIELD_VALUE_RE, MY_FIRST_NAME, MY_LAST_NAME, MY_FULL_NAME,
    MY_EMAIL, MY_PHONE, MY_COMPANY, MY_WEBSITE, MY_JOB_TITLE
)
from .browser import create_playwright_context, get_page_content, highlight_detected_fields
from .discovery import discover_contact_url
from .forms import get_all_fields, fill_form, js_fallback_fill, ensure_required_checks, click_submit_button
from .captcha import detect_and_solve_captcha
from .llm import generate_pitch_and_subject, request_form_fill_plan, extract_identity_from_instructions, request_combined_outreach_plan
from .submission import analyze_submission_result
from .tracking import token_tracker
from .sheets import append_to_sheet

class OutreachWorker:
    def __init__(self, worker_index, campaign_id=None, proxy=None, wks=None, search_enabled=False):
        self.worker_index = worker_index
        self.campaign_id = campaign_id
        self.proxy = proxy
        self.wks = wks
        self.search_enabled = search_enabled
        self.browser_context = None
        self.is_visual = os.environ.get("HEADLESS_BROWSER", "true").lower() == "false"

    def _extract_lead_data(self, lead):
        """Robustly extract company name and website from lead dict."""
        # Try multiple common key variants for company name
        company = (
            lead.get("company_name") or 
            lead.get("Company Name") or 
            lead.get("name") or 
            lead.get("company") or 
            "Unknown"
        )
        # Try multiple common key variants for website
        website = (
            lead.get("website") or 
            lead.get("Website") or 
            lead.get("url") or 
            lead.get("website_url") or 
            ""
        )
        return str(company).strip(), str(website).strip()

    async def run(self, lead):
        company_name, website = self._extract_lead_data(lead)
        
        print(f"\n [Worker {self.worker_index}] [INPUT] Processing Company: {company_name} | Website: {website}")
        
        start_time = time.time()
        contact_url = website # Default
        discovery_meta = "direct"
        success = False
        reason = "Init"
        captcha_status = "none"
        fields = []
        pitch = ""
        subject = ""
        
        try:
            async with create_playwright_context(proxy=self.proxy, worker_index=self.worker_index) as (browser, context):
                page = await context.new_page()
                
                # 1. Discovery
                if website:
                    print(f" [Worker {self.worker_index}] [STEP 1] Starting Discovery for {website}...")
                    try:
                        await page.goto(website, timeout=15000, wait_until="domcontentloaded")
                    except Exception as e:
                        print(f" [Worker {self.worker_index}] [STEP 1] Warning: Initial homepage load failed: {e}")
                    
                    contact_url, discovery_meta, discovery_success = await discover_contact_url(page, website, search_enabled=self.search_enabled)
                    if discovery_success:
                        print(f" [Worker {self.worker_index}] [STEP 1] Found contact page: {contact_url} ({discovery_meta})")
                    else:
                        print(f" [Worker {self.worker_index}] [STEP 1] Discovery failed. Aborting lead.")
                        return {
                            "company_name": company_name,
                            "contact_url": website,
                            "submitted": "No",
                            "submission_assurance": "Contact form not found during discovery.",
                            "submission_status": "Failed",
                            "discovery_strategy": discovery_meta,
                            "step_index": int(os.environ.get("OUTREACH_STEP", "0"))
                        }

                # 2. Field Identification
                # Skip navigation if discovery already landed us on the right page
                current_url = page.url.split('#')[0].rstrip('/')
                target_url = contact_url.split('#')[0].rstrip('/')

                if current_url != target_url:
                    print(f" [Worker {self.worker_index}] [STEP 2] Navigating to {contact_url}...")
                    try:
                        await page.goto(contact_url, wait_until="networkidle", timeout=30000)
                    except Exception as e:
                        print(f" [Worker {self.worker_index}] [STEP 2] Navigation issue: {e}")
                else:
                    print(f" [Worker {self.worker_index}] [STEP 2] Already on discovered page. Proceeding to identification.")
                    # Give it a moment to finish any lazy-loading scripts
                    await asyncio.sleep(1) 
                
                fields = await get_all_fields(page)
                print(f" [Worker {self.worker_index}] [STEP 2] Identified {len(fields)} form fields.")
                
                if self.is_visual:
                    try:
                        await highlight_detected_fields(page, fields)
                        await asyncio.sleep(2) # Pause to let user see highlights
                    except Exception as e:
                        print(f" [Worker {self.worker_index}] [VISUAL] Highlight failed: {e}")

                # 3. Captcha Shield
                print(f" [Worker {self.worker_index}] [STEP 3] Checking for Captcha...")
                captcha_status = await detect_and_solve_captcha(page)
                print(f" [Worker {self.worker_index}] [STEP 3] Captcha status: {captcha_status}")

                # 4. Identity & AI Reasoning (Pitch + Plan)
                step_idx = int(os.environ.get("OUTREACH_STEP", "0"))
                from .db import fetch_campaign_data
                campaign_data = fetch_campaign_data(self.campaign_id)

                steps = campaign_data.get("steps", [])

                print(f" [Worker {self.worker_index}] [STEP 4] Campaign Data: {campaign_data}")
                print(f" [Worker {self.worker_index}] [STEP 4] Steps: {steps}")
                
                step_instructions = ""
                if 0 <= step_idx < len(steps):
                    step_instructions = steps[step_idx].get("details") or steps[step_idx].get("aiInstruction") or ""
                
                print(f" [Worker {self.worker_index}] [STEP 4] Step Instructions: {step_instructions}")
                print(f" [Worker {self.worker_index}] [STEP 4] Extracting Identity from Step Details...")
                persona = await extract_identity_from_instructions(step_instructions, worker_index=self.worker_index)
                
                # Fallback: if GPT extraction failed or returned nothing, use global config
                if not persona or not any(persona.values()):
                    print(f" [Worker {self.worker_index}] [STEP 4] GPT Identity extraction returned nothing. Using fallback from config.")
                    from .config import MY_FIRST_NAME, MY_LAST_NAME, MY_FULL_NAME, MY_EMAIL, MY_PHONE, MY_COMPANY, MY_WEBSITE, MY_JOB_TITLE, MY_ADDRESS
                    persona = {
                        "first_name": MY_FIRST_NAME,
                        "last_name": MY_LAST_NAME,
                        "full_name": MY_FULL_NAME,
                        "email": MY_EMAIL,
                        "phone": MY_PHONE,
                        "company": MY_COMPANY,
                        "website": MY_WEBSITE,
                        "job_title": MY_JOB_TITLE,
                        "address": MY_ADDRESS
                    }
                
                if persona:
                    print(f" [Worker {self.worker_index}] [STEP 4] Identity/Persona Details:")
                    for k, v in persona.items():
                        if v: print(f"      - {k}: {v}")
                else:
                    print(f" [Worker {self.worker_index}] [STEP 4] Warning: No persona details found.")

                # 3. Captcha
                captcha_status = "none"
                try:
                    print(f" [Worker {self.worker_index}] [STEP 3] Checking for Captcha...")
                    captcha_status = await detect_and_solve_captcha(page)
                    print(f" [Worker {self.worker_index}] [STEP 3] Captcha status: {captcha_status}")
                except Exception as e:
                    print(f" [Worker {self.worker_index}] [STEP 3] Error during captcha check: {e}")

                # 4. Generate Outreach Plan & Fill
                filled_details = []
                subject = f"Inquiry for {company_name}"
                pitch = ""
                
                try:
                    if not fields:
                        raise ValueError("No form fields detected.")

                    print(f" [Worker {self.worker_index}] [STEP 4] Generating tailored outreach plan for {company_name}...")
                    outreach_plan = await request_combined_outreach_plan(
                        company_name,
                        instructions=step_instructions,
                        fields=fields,
                        persona=persona,
                        worker_index=self.worker_index
                    )

                    print(f" [Worker {self.worker_index}] [STEP 4] Outreach Plan: {outreach_plan}")
                    
                    subject = outreach_plan.get("subject", subject)
                    pitch = outreach_plan.get("pitch", "")
                    plan_actions = outreach_plan.get("actions", [])
                    
                    print(f" [Worker {self.worker_index}] [STEP 4] Plan received: Actions={len(plan_actions)}")
                    
                    if plan_actions:
                        _, filled_details = await fill_form(page, plan_actions, persona=persona)
                        await ensure_required_checks(page)
                        if self.is_visual and self.worker_index == 0:
                            await asyncio.sleep(2)
                    else:
                        print(f" [Worker {self.worker_index}] [STEP 4] GPT Plan is empty. Attempting JS fallback and pitch generation.")
                        try:
                            # Ensure we have a non-empty pitch/subject via fallback
                            if not pitch or not subject:
                                print(f" [Worker {self.worker_index}] [STEP 4] Generating pitch/subject via fallback...")
                                pitch_fb, subject_fb = await generate_pitch_and_subject(
                                    company_name, instructions=step_instructions, fields=fields, persona=persona, worker_index=self.worker_index
                                )
                                pitch = pitch_fb or pitch
                                subject = subject_fb or subject

                            # Run JS heuristic filler as a fallback when plan_actions is empty
                            filled_count, filled = await js_fallback_fill(page, pitch, subject, persona=persona)
                            if filled_count:
                                filled_details = filled
                            await ensure_required_checks(page)
                            if self.is_visual and self.worker_index == 0:
                                await asyncio.sleep(2)
                        except Exception as e:
                            print(f" [Worker {self.worker_index}] [STEP 4] JS fallback failed: {e}")
                except Exception as e:
                    print(f" [Worker {self.worker_index}] [STEP 4] Error during plan/fill: {e}")
                    # If we have no fields, we return early as a defined failure
                    if "No form fields" in str(e):
                        return {
                            "company_name": company_name, "contact_url": contact_url, 
                            "submitted": "No", "submission_assurance": str(e),
                            "submission_status": "Failed", "discovery_strategy": discovery_meta,
                            "step_index": step_idx
                        }

                # 5. Submit
                success, reason = False, "Unknown failure"
                try:
                    print(f" [Worker {self.worker_index}] [STEP 5] Attempting form submission...")
                    await click_submit_button(page)
                    
                    # 6. Analyze Result
                    print(f" [Worker {self.worker_index}] [STEP 6] Verifying submission result...")
                    await asyncio.sleep(5) # Wait for page lead/navigation
                    content = await page.content()
                    success, reason = analyze_submission_result(content, page.url, contact_url)
                except Exception as e:
                    reason = f"Submission process error: {e}"
                    print(f" [Worker {self.worker_index}] [STEP 5/6] Error: {reason}")
                
                duration = time.time() - start_time
                print(f" [Worker {self.worker_index}] [STEP 6] Final Verdict: {'Success' if success else 'Failed'} | Reason: {reason}")
                if self.is_visual and self.worker_index == 0: await asyncio.sleep(3) # Pause at the end to see the result page
                
                # Create a structured fields data object
                fields_data = {
                    "identified": fields,
                    "filled": filled_details
                }
                fields_json = json.dumps(fields_data)

                # 7. Logging - Aligning with Back.py Result Schema
                step_idx = int(os.environ.get("OUTREACH_STEP", "0"))
                result = {
                    "company_name": company_name,
                    "contact_url": contact_url,
                    "submitted": "Yes" if success else "No",
                    "submission_assurance": reason,
                    "captcha_status": str(captcha_status or "none"),
                    "proxy_used": f"worker-{self.worker_index}",
                    "bandwidth_kb": "0",
                    "run_timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "submission_status": "Success" if success else "Failed",
                    "confirmation_msg": reason,
                    "message_sent": str(pitch or "")[:500] if success else "-",
                    "fields_filled": fields_json, # Structured data
                    "step_index": step_idx,
                    "discovery_strategy": discovery_meta
                }
                
                if self.wks:
                    row = [
                        result["run_timestamp"], company_name, website, contact_url,
                        result["submitted"], result["submission_assurance"]
                    ]
                    await append_to_sheet(self.wks, row)
                
                return result
                
        except Exception as e:
            import traceback
            # Only print traceback if it's a critical logic error
            print(f" [Worker {self.worker_index}] !! Step Execution Interrupted: {e}")
            traceback.print_exc()
            return {
                "company_name": company_name,
                "contact_url": contact_url,
                "submitted": "No",
                "submission_assurance": f"Error: {str(e)}",
                "run_timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
            }
