import psycopg2
import psycopg2.extras

DATABASE_URL = "postgresql://postgres.rhmqhrjbknazyflmbwbv:6%3F9H%23%40Dv5W%2BVTEZ@aws-1-ap-northeast-2.pooler.supabase.com:6543/postgres"

_REPORT_SQL = """
SELECT
  COUNT(*)                                                        AS total_data,
  COALESCE(SUM(bandwidth_kb) / 1024, 0)                         AS proxy_bandwidth_mb,
  COUNT(*) FILTER (WHERE form_found = TRUE)                      AS contact_form_present,
  COUNT(*) FILTER (WHERE captcha_present = TRUE)                 AS captcha_present_count,
  COUNT(*) FILTER (WHERE form_found = TRUE AND captcha_present = FALSE) AS without_captcha,
  COUNT(*) FILTER (WHERE submitted = 'Yes')                      AS total_successful,
  COUNT(*) FILTER (WHERE submitted = 'No')                       AS total_errors,
  -- Without Captcha breakdown
  COUNT(*) FILTER (WHERE captcha_present = FALSE AND submitted = 'Yes')                    AS wc_successful,
  COUNT(*) FILTER (WHERE captcha_present = FALSE AND status = 'submission_not_confirmed')  AS wc_not_confirmed,
  COUNT(*) FILTER (WHERE captcha_present = FALSE AND status = 'form_validation_failed')    AS wc_validation_failed,
  COUNT(*) FILTER (WHERE captcha_present = FALSE AND status = 'submit_button_not_found')   AS wc_no_submit,
  COUNT(*) FILTER (WHERE captcha_present = FALSE AND status = 'website_error')             AS wc_website_error,
  COUNT(*) FILTER (WHERE captcha_present = FALSE AND status = 'invalid_field_value')       AS wc_invalid_field,
  COUNT(*) FILTER (WHERE captcha_present = FALSE AND http_status_code = 403)               AS wc_403,
  COUNT(*) FILTER (WHERE captcha_present = FALSE AND http_status_code = 404)               AS wc_404,
  COUNT(*) FILTER (WHERE captcha_present = FALSE AND status = 'rate_limited')              AS wc_rate_limited,
  COUNT(*) FILTER (WHERE captcha_present = FALSE AND http_status_code = 423)               AS wc_423,
  -- Captcha breakdown
  COUNT(*) FILTER (WHERE captcha_present = TRUE AND submitted = 'Yes')                                          AS cap_successful,
  COUNT(*) FILTER (WHERE captcha_present = TRUE AND captcha_type = 'cloudflare')                                AS cap_cloudflare,
  COUNT(*) FILTER (WHERE captcha_present = TRUE AND captcha_type = 'recaptcha2' AND captcha_result = 'timeout') AS cap_rc2_timeout,
  COUNT(*) FILTER (WHERE captcha_present = TRUE AND captcha_type = 'recaptcha2' AND captcha_result = 'no_sitekey') AS cap_rc2_nositekey,
  COUNT(*) FILTER (WHERE captcha_present = TRUE AND captcha_type = 'recaptcha3' AND captcha_result = 'timeout') AS cap_rc3_timeout,
  COUNT(*) FILTER (WHERE captcha_present = TRUE AND captcha_type = 'recaptcha3' AND captcha_result = 'no_sitekey') AS cap_rc3_nositekey,
  COUNT(*) FILTER (WHERE captcha_present = TRUE AND captcha_type = 'turnstile'  AND captcha_result = 'timeout') AS cap_ts_timeout,
  COUNT(*) FILTER (WHERE captcha_present = TRUE AND captcha_type = 'turnstile'  AND captcha_result = 'no_sitekey') AS cap_ts_nositekey,
  COUNT(*) FILTER (WHERE captcha_present = TRUE AND captcha_type = 'hcaptcha'   AND captcha_result = 'timeout') AS cap_hc_timeout,
  COUNT(*) FILTER (WHERE captcha_present = TRUE AND captcha_type = 'hcaptcha'   AND captcha_result = 'no_sitekey') AS cap_hc_nositekey,
  COUNT(*) FILTER (WHERE captcha_present = TRUE AND status = 'invalid_field_value')                             AS cap_invalid_field,
  COUNT(*) FILTER (WHERE captcha_present = TRUE AND status = 'website_error')                                   AS cap_website_error
FROM outreach_results
"""

try:
    conn = psycopg2.connect(DATABASE_URL)
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        # Check if table exists
        cur.execute("SELECT 1 FROM outreach_results LIMIT 1")
        print("Table outreach_results exists.")
        
        # Run report query
        cur.execute(_REPORT_SQL)
        row = cur.fetchone()
        print("Query result:", dict(row) if row else "No results")
except Exception as e:
    print("ERROR:", e)
finally:
    if 'conn' in locals():
        conn.close()
