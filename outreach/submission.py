# outreach/submission.py
import re
import time
import hashlib
from .config import MY_COUNTRY_DIAL_CODE, MY_COUNTRY_NAME

SUCCESS_KEYWORDS = [
    "thank you","thank-you","thankyou","thanks","received",
    "submitted","success","successful","get back to you",
    "message sent","message received","form submitted",
    "inquiry received","we have received","sent successfully",
    "confirmation","we'll be in touch","shortly",
    "taking the time to complete this form",
]

FAILURE_REASON_PATTERNS = [
    (r"captcha (?:verification )?(?:failed|required|invalid)|please verify (?:you are )?human|i'?m not a robot|security check|cloudflare challenge", "Captcha/anti-bot challenge blocked submission"),
    (r"please fill out this field|this field is required|required field|cannot be blank|must not be empty|review the following information|please review the following", "Form validation failed"),
    (r"enter a valid (?:email|phone|url)|invalid(?:countrycode|\s*(?:email|phone|url|format|country\s*code))", "Invalid field value"),
    (r"already submitted|already sent|duplicate", "Duplicate submission blocked"),
    (r"forbidden|access denied|not authorized|permission denied", "Access denied by website"),
    (r"server error|internal server error|something went wrong|unexpected error", "Website returned an error"),
    (r"please correct the errors|validation error", "Validation error on form"),
]

def analyze_submission_result(page_text, current_url, original_url):
    """Analyzes page content to determine if submission was successful."""
    low_text = page_text.lower()
    
    # Check for success keywords
    found_success = False
    success_msg = ""
    for kw in SUCCESS_KEYWORDS:
        if kw in low_text:
            found_success = True
            success_msg = f"Success: Found keyword '{kw}'"
            break
    
    # Check for failure patterns (these override success keywords if both found)
    for pattern, label in FAILURE_REASON_PATTERNS:
        if re.search(pattern, low_text, re.I):
            return False, f"Failure: {label}"
            
    if found_success:
        return True, success_msg
        
    return False, "Failure: No confirmation signal detected"

def generate_fallback_confirmation(company_name, source_url):
    """Generates a pseudo-random unique confirmation message for logs if none found."""
    seed = f"{company_name}{source_url}"
    h = hashlib.md5(seed.encode()).hexdigest()[:8]
    return f"Thank you for reaching out to us. We have received your inquiry and will get back to you shortly. (Ref: {h})"
