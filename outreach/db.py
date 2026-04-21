# outreach/db.py
import os
import json
import psycopg2
import psycopg2.extras

def fetch_campaign_data(campaign_id: str = None):
    """
    Fetches campaign-level settings (search_for_form, steps) from the database.
    Falls back to environment variables for local testing.
    """
    db_url = os.environ.get("DATABASE_URL")
    
    # 1. Try Database if campaign_id is available
    if campaign_id and db_url:
        try:
            conn = psycopg2.connect(db_url)
            with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
                cur.execute("SELECT search_for_form, steps FROM campaigns WHERE campaign_id = %s", (campaign_id,))
                row = cur.fetchone()
                if row:
                    steps = row["steps"]
                    if isinstance(steps, str):
                        try:
                            steps = json.loads(steps)
                        except Exception:
                            steps = []
                    
                    return {
                        "search_for_form": bool(row["search_for_form"]),
                        "steps": [s for s in (steps or []) if isinstance(s, dict) and s.get("enabled") is not False]
                    }
        except Exception as e:
            print(f" [DB] Warning: Could not fetch campaign data for '{campaign_id}': {e}")
        finally:
            if 'conn' in locals(): conn.close()

    # 2. Fallback to Environment Variables (for testing/legacy runs)
    search_enabled = str(os.environ.get("SEARCH_FOR_FORM", "0")).strip().lower() in {"1", "true", "yes"}
    env_steps = []
    try:
        raw_steps = os.environ.get("CAMPAIGN_STEPS", "")
        if raw_steps:
            parsed = json.loads(raw_steps)
            env_steps = [s for s in parsed if isinstance(s, dict) and s.get("enabled") is not False]
    except Exception as e:
        print(f" [DB] Env Fallback Warning: Failed to parse CAMPAIGN_STEPS: {e}")

    return {
        "search_for_form": search_enabled,
        "steps": env_steps
    }
