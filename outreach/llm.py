# outreach/llm.py
import os
import json
import re
from openai import AsyncOpenAI
from .config import (
    OPENAI_API_KEY, OPENAI_FORM_FILL_MODEL, 
    FORM_FILL_MAX_INPUT_TOKENS, FORM_FILL_MAX_OUTPUT_TOKENS,
    FORM_FILL_FIELD_CATALOG_LIMIT, MY_TITLE, MY_COMPANY
)
from .utils import extract_json_candidate, sanitize_pitch_text
from .tracking import token_tracker

openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

async def extract_identity_from_instructions(instructions: str, worker_index=-1):
    """
    Exracts sender identity from campaign instructions.
    Returns a dict with name, email, phone, company, etc.
    """

    # print("instructions", instructions)
    if not openai_client or not instructions:
        return {}
        
    prompt = (
        "Extract the sender identity details from these outreach instructions. "
        "The instructions describe WHO is sending the message. "
        "Instructions: " + instructions + "\n\n"
        "Return a JSON object with these keys: first_name, last_name, full_name, email, phone, company, website, job_title, address. "
        "If a field is mentioned (e.g., 'I am Varun' or 'My email is...'), extract it. Use empty strings for missing fields."
    )
    
    try:
        response = await openai_client.chat.completions.create(
            model=OPENAI_FORM_FILL_MODEL,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"}
        )
        token_tracker.record("System", "identity_extraction", response.usage, worker_index)
        content = response.choices[0].message.content
        try:
            return json.loads(extract_json_candidate(content))
        except Exception:
            print(f" [LLM] Failed to parse identity JSON. Raw content: {content[:200]}...")
            return {}
    except Exception as e:
        print(f" [LLM] Error extracting identity: {e}")
        return {}

async def generate_pitch_and_subject(company_name, instructions="", fields=None, persona=None, worker_index=-1):
    if not openai_client: return "", ""
    
    persona_context = ""
    if persona:
        persona_context = f"\nYour (The Sender's) Identity:\n{json.dumps(persona, indent=2)}"

    fields_desc = ""
    if fields:
        fields_summary = [f"{f.get('label') or f.get('name') or 'field'} ({f.get('type')})" for f in fields[:10]]
        fields_desc = f"\nThe contact form has these fields: {', '.join(fields_summary)}."

    prompt = (
        f"You are a professional outreach assistant. Your goal is to write a short, highly personalized and unique message (pitch) for {company_name}."
        f"\n\nCampaign Context/Instructions:\n{instructions}"
        f"{persona_context}"
        f"{fields_desc}"
        f"\n\nCRITICAL: Make the message unique and tailored specifically to what you think {company_name} does based on their name."
        f"\nReturn the subject and pitch separately. Ensure the tone is professional but personalized."
    )
    
    try:
        response = await openai_client.chat.completions.create(
            model=OPENAI_FORM_FILL_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_completion_tokens=400
        )
        token_tracker.record(company_name, "pitch", response.usage, worker_index)
        content = response.choices[0].message.content
        
        lines = [l.strip() for l in content.split("\n") if l.strip()]
        subj = f"Inquiry for {company_name}"
        body = content
        if lines and ("Subject:" in lines[0] or "Subject" in lines[0]):
            subj = lines[0].replace("Subject:", "").replace("Subject", "").strip()
            body = "\n".join(lines[1:])
        
        return sanitize_pitch_text(body), subj
    except Exception as exc:
        return "", f"Inquiry for {company_name}"

def build_gpt_prompt(company_name, pitch, subject, fields, persona=None):
    prompt = f"TARGET COMPANY: {company_name}\n\n"
    if persona:
        prompt += f"SENDER IDENTITY:\n{json.dumps(persona, indent=2)}\n\n"
    prompt += f"SUBJECT: {subject}\nPITCH: {pitch}\n\n"
    prompt += f"FORM FIELDS (JSON):\n{json.dumps(fields[:FORM_FILL_FIELD_CATALOG_LIMIT], indent=2)}\n\n"
    prompt += "TASK: Create a filling plan for the form fields above using the pitch and identity provided.\n"
    prompt += "Return a JSON object with an 'actions' key containing a list of {sel, val, label} objects.\n"
    prompt += f"Assign the SUBJECT to the subject field (if any). Assign the PITCH to the main message/comment/requirements/details textarea field.\n"
    prompt += "Assign the donor/user's identity details (name, email, etc.) to the respective fields.\n"
    prompt += "The 'sel' must be the exact CSS selector from the fields JSON. 'val' is the value to fill. 'label' is the field label.\n"
    prompt += "For checkboxes (legal, consent, terms), set 'val' to true (boolean).\n"
    prompt += "Strictly output ONLY the JSON object with an 'actions' array. Example: { \"actions\": [ { \"sel\": \"#email\", \"val\": \"...\", \"label\": \"...\" } ] }\n"
    prompt += "Be helpful and ensure all required-looking fields are covered."
    return prompt

async def request_form_fill_plan(company_name, pitch, subject, fields, persona=None, worker_index=-1):
    prompt_text = build_gpt_prompt(company_name, pitch, subject, fields, persona)
    if not openai_client: return []
    
    try:
        response = await openai_client.chat.completions.create(
            model=OPENAI_FORM_FILL_MODEL,
            messages=[{"role": "user", "content": prompt_text}],
            max_completion_tokens=4096,
            response_format={"type": "json_object"}
        )
        token_tracker.record(company_name, "fill", response.usage, worker_index)
        raw = response.choices[0].message.content
        if not raw:
            return []
            
        try:
            # Try direct load first (since json_object mode is on)
            parsed = json.loads(raw)
            return parsed.get("actions", [])
        except json.JSONDecodeError:
            try:
                # Fallback to extraction if there is leading/trailing text
                candidate = extract_json_candidate(raw)
                parsed = json.loads(candidate)
                return parsed.get("actions", [])
            except Exception as e:
                print(f" [LLM] Failed to parse plan JSON even with extraction: {e}")
                print(f" [LLM] Raw content preview: {raw[:500]}...")
                return []
    except Exception as exc:
        print(f" [LLM] OpenAI request failed: {exc}")
        return []
async def request_combined_outreach_plan(company_name, instructions, fields, persona=None, worker_index=-1):
    """
    Combines pitch generation and form filling into a single atomic operation.
    Reduces total token usage and latency by eliminating redundant system prompts.
    """
    if not openai_client:
        return {"subject": f"Inquiry for {company_name}", "pitch": "", "actions": []}
        
    persona_context = ""
    if persona:
        persona_context = f"\nSENDER IDENTITY:\n{json.dumps(persona, indent=2)}"

    prompt = (
        f"You are a master of outreach and form automation. Your task is to craft a conversion-focused inquiry and design a COMPLETE form-filling plan for {company_name}.\n\n"
        f"CAMPAIGN OBJECTIVE & INSTRUCTIONS:\n{instructions}\n"
        f"{persona_context}\n\n"
        f"FORM FIELDS TO FILL (JSON Catalog):\n{json.dumps(fields[:FORM_FILL_FIELD_CATALOG_LIMIT], indent=2)}\n\n"
        "REQUIREMENTS (READ CAREFULLY):\n"
        "- You MUST return EXACTLY one valid JSON object and NOTHING else. No explanation, no markdown, no code fences.\n"
        "- The JSON object MUST contain these keys: 'subject' (non-empty string), 'pitch' (non-empty string), and 'actions' (array).\n"
        "- The 'actions' array MUST contain one object for EVERY field in the provided JSON catalog. Do NOT omit fields.\n"
        "- Each action object MUST have keys: 'sel' (exact selector from the catalog), 'val' (the value to type; if unknown, supply a sensible default or an empty string), and 'label' (the field label).\n"
        "- For checkbox/boolean fields set 'val' to true or false (boolean), NOT strings.\n"
        "- 'subject' and 'pitch' MUST be non-empty. If you cannot produce a highly tailored pitch, create a short (1-3 sentence) professional generic pitch.\n"
        "- If a value is not available from identity, infer a reasonable answer from the company name or campaign context rather than leaving fields out.\n\n"
        "OUTPUT EXAMPLE (MUST MATCH STRUCTURE EXACTLY):\n"
        "{\"subject\":\"Inquiry for ExampleCo\",\"pitch\":\"Hi ExampleCo, we can help...\",\"actions\":[{\"sel\":\"#email\",\"val\":\"me@example.com\",\"label\":\"Work E-Mail\"}]}\n\n"
        "Do not include any additional keys, comments, or text. Produce valid JSON only."
    )

    # print(f" [LLM] Prompt: {prompt}")

    try:
        response = await openai_client.chat.completions.create(
            model=OPENAI_FORM_FILL_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_completion_tokens=4096,
            response_format={"type": "json_object"}
        )
        token_tracker.record(company_name, "combined_outreach", response.usage, worker_index)
        # Try primary content field
        try:
            raw = response.choices[0].message.content
        except Exception:
            raw = None

        # DEBUG LOGGING for empty results investigation
        print(f" [LLM] Raw Response from {OPENAI_FORM_FILL_MODEL}: {raw}")

        # If content is empty but tokens were consumed, attempt to inspect full response
        if not raw:
            try:
                rep = str(response)
                print(f" [LLM] Full response repr (truncated): {rep[:2000]}")
                # Try to locate JSON-like substrings in full repr as a last resort
                if not raw:
                    cand = extract_json_candidate(rep)
                    if cand and (cand.strip().startswith('{') or cand.strip().startswith('[')):
                        raw = cand
                        print(" [LLM] Extracted JSON-like candidate from full response repr.")
            except Exception as e:
                print(f" [LLM] Could not stringify response for debug: {e}")
        
        if not raw:
            return {"subject": f"Inquiry for {company_name}", "pitch": "", "actions": []}

        # Try direct JSON parse first
        try:
            parsed = json.loads(raw)
            res = {
                "subject": parsed.get("subject", "").strip() or f"Inquiry for {company_name}",
                "pitch": sanitize_pitch_text(parsed.get("pitch", "")),
                "actions": parsed.get("actions", [])
            }
            return res
        except Exception:
            # Attempt to extract JSON-like candidate from raw text (handles code fences and extra commentary)
            try:
                candidate = extract_json_candidate(raw)
                parsed = json.loads(candidate)
                res = {
                    "subject": parsed.get("subject", "").strip() or f"Inquiry for {company_name}",
                    "pitch": sanitize_pitch_text(parsed.get("pitch", "")),
                    "actions": parsed.get("actions", [])
                }
                return res
            except Exception as e:
                print(f" [LLM] Failed to parse combined plan JSON after extraction: {e}")

        # Heuristic fallback: extract Subject and Pitch from raw text if present
        subj = None
        p = None
        try:
            # Look for lines like 'Subject: ...' or 'Subject - ...'
            m_sub = re.search(r"^\s*Subject\s*[:\-]\s*(.+)$", raw, re.IGNORECASE | re.MULTILINE)
            if m_sub:
                subj = m_sub.group(1).strip()

            # Look for 'Pitch:' or large paragraph after header 'Pitch' or 'Message'
            m_pitch = re.search(r"^\s*(?:Pitch|Message|Body)\s*[:\-]\s*([\s\S]{20,2000})$", raw, re.IGNORECASE | re.MULTILINE)
            if m_pitch:
                p = sanitize_pitch_text(m_pitch.group(1).strip())

            # If subject/pitch found, also try to extract an actions array candidate
            actions = []
            arr_match = re.search(r"\"actions\"\s*:\s*(\[.*\])", raw, re.IGNORECASE | re.DOTALL)
            if arr_match:
                try:
                    actions = json.loads(arr_match.group(1))
                except Exception:
                    try:
                        actions = json.loads(extract_json_candidate(arr_match.group(1)))
                    except Exception:
                        actions = []

            return {
                "subject": (subj or f"Inquiry for {company_name}"),
                "pitch": (p or ""),
                "actions": actions
            }
        except Exception as e:
            print(f" [LLM] Heuristic extraction failed: {e}")
            return {"subject": f"Inquiry for {company_name}", "pitch": "", "actions": []}
    except Exception as exc:
        print(f" [LLM] Combined outreach request failed: {exc}")
        return {"subject": f"Inquiry for {company_name}", "pitch": "", "actions": []}
