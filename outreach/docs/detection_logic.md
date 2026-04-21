# Outreach Engine: Detection Logic Breakdown

This document explains how the outreach engine identifies form fields and makes decisions on how to fill them.

## 1. Field Extraction (`EXTRACT_FIELDS_JS`)

The engine uses a sophisticated JavaScript-based extraction script to find interactive elements on the page. Unlike simple scrapers, it considers visibility, accessibility, and modern web patterns.

### Key Strategies:
- **Shadow DOM Piercing**: Many modern websites (like those using Web Components) hide their internal structure in Shadow Roots. The engine recursively traverses every shadow root to ensure no fields are missed.
- **Visibility Checks**: It uses `getBoundingClientRect()` and `getComputedStyle()` to confirm that a field is actually visible and has a physical size. This prevents the bot from filling "honeypot" fields (hidden fields meant to catch bots).
- **Label Identification**:
    - **Aria-Labels**: Checks for `aria-label` or `placeholder`.
    - **Associated Labels**: Finds `<label for="id">` elements.
    - **Proximity Search**: If no explicit label exists, it searches the parent tree for nearby text elements (SPANS, Ps, DIVs) that likely represent the field's label.

### Filtering (Avoiding Noise):
The engine explicitly ignores:
- Search bars (based on IDs like `s`, `search`, `sf_s`).
- Navigation links.
- Bot-trap keywords like `bot-trap`, `honeypot`, or `leave-blank`.
- Captcha-related fields (these are handled separately).

## 2. AI-Driven Filling Plan (`request_form_fill_plan`)

Once the raw list of fields is extracted, the engine doesn't just "guess." It uses GPT-4o (or the configured model) to create an execution plan.

### The Mapping Process:
1. **Cataloging**: The engine creates a JSON representation of all detected fields (Type, ID, Name, Label).
2. **Context Injection**: It provides the AI with:
    - The target company name.
    - Your generated subject and pitch.
    - Your personal/company details (Name, Website, Email).
3. **Reasoning**: The AI analyzes each detected field and decides which value from your profile matches it best.
    - *Example*: A field labeled "How can we help?" might be mapped to your `pitch`.
    - *Example*: A field labeled "Full Name" is mapped to `MY_FULL_NAME`.

## 3. Interaction & Submission

### React/Vue Safety:
Modern frameworks often fail if you simply set `.value = "..."`. The engine uses a custom script (`REACT_FILL_JS`) that:
1. Sets the value via the internal prototype setter.
2. Dispatches `input`, `change`, `blur`, `keyup`, and `keydown` events.
3. Dispatches a native `InputEvent`.
This ensures the website's state manager (like Redux or React State) actually updates.

### Required Checks:
Before submitting, the engine automatically finds and clicks required checkboxes (like "I agree to the privacy policy") that are often missed by simple automation.

### Result Verification:
After clicking "Submit," the engine doesn't just assume success. It analyzes the resulting page content and URL for:
- Success keywords ("Thank you," "Success," "Message sent").
- URL changes (redirection to a `/thanks` page).
- Persistent forms (if the form is still there with errors, it marks it as a failure).
