from __future__ import annotations

import csv
import os
import json
import re
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import error as urlerror
from urllib import request as urlrequest
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Query, Request, Header, Body
from pydantic import BaseModel, Field
import psycopg2
import psycopg2.extras
from psycopg2 import pool


BASE_DIR = Path(__file__).resolve().parent
OUTREACH_SCRIPT = BASE_DIR / "Outreach(1).py"
LOG_BUFFER_SIZE = 1000
RESULT_PREFIX = "[RESULT]"
SOCIAL_URL_DETAIL = "Social media URLs are not allowed for outreach leads"
SOCIAL_MEDIA_DOMAINS = {
	"facebook.com",
	"fb.com",
	"linkedin.com",
	"instagram.com",
	"twitter.com",
	"x.com",
	"t.co",
	"youtube.com",
	"youtu.be",
	"tiktok.com",
	"pinterest.com",
	"reddit.com",
	"snapchat.com",
	"whatsapp.com",
	"wa.me",
	"telegram.me",
	"t.me",
	"discord.com",
}


DEFAULT_DATABASE_URL = "postgresql://postgres.rhmqhrjbknazyflmbwbv:6%3F9H%23%40Dv5W%2BVTEZ@aws-1-ap-northeast-2.pooler.supabase.com:6543/postgres"

def _resolve_database_url() -> str:
	for key in ("DATABASE_URL", "DATABASE_STRING", "POSTGRES_URL", "SUPABASE_DB_URL"):
		candidate = str(os.environ.get(key, "") or "").strip()
		if candidate:
			return candidate
	return DEFAULT_DATABASE_URL

DATABASE_URL = _resolve_database_url()
_db_pool: pool.SimpleConnectionPool | None = None
_db_available = False
_db_init_error: str | None = None

def _init_db() -> None:
	global _db_available, _db_init_error, _db_pool
	try:
		_db_pool = pool.SimpleConnectionPool(1, 10, DATABASE_URL)
		conn = _db_pool.getconn()
		with conn.cursor() as cur:
			# Runs table
			cur.execute("""
				CREATE TABLE IF NOT EXISTS outreach_runs (
					run_id TEXT PRIMARY KEY,
					status TEXT NOT NULL,
					pid INTEGER,
					csv_path TEXT,
					started_at TEXT,
					finished_at TEXT,
					campaign_id TEXT,
					campaign_title TEXT,
					total_leads INTEGER DEFAULT 0,
					processed_leads INTEGER DEFAULT 0,
					duplicates_skipped INTEGER DEFAULT 0,
					resume_skipped_leads INTEGER DEFAULT 0,
					social_skipped_leads INTEGER DEFAULT 0,
					resumed_from_run_id TEXT,
					exit_code INTEGER
				)
			""")
			# Logs table
			cur.execute("""
				CREATE TABLE IF NOT EXISTS outreach_logs (
					id SERIAL PRIMARY KEY,
					run_id TEXT NOT NULL,
					line TEXT NOT NULL,
					created_at TEXT NOT NULL
				)
			""")
			# Migration: add fields_filled_data to campaign_contacts if missing
			cur.execute("""
				DO $$ BEGIN
					IF NOT EXISTS (
						SELECT 1 FROM information_schema.columns
						WHERE table_name = 'campaign_contacts' AND column_name = 'fields_filled_data'
					) THEN
						ALTER TABLE campaign_contacts ADD COLUMN fields_filled_data TEXT DEFAULT '';
					END IF;
				END $$;
			""")
			# Migration: add fields_filled_data to submission_logs if missing
			cur.execute("""
				DO $$ BEGIN
					IF NOT EXISTS (
						SELECT 1 FROM information_schema.columns
						WHERE table_name = 'submission_logs' AND column_name = 'fields_filled_data'
					) THEN
						ALTER TABLE submission_logs ADD COLUMN fields_filled_data TEXT DEFAULT '';
					END IF;
				END $$;
			""")
			# Runs table
			cur.execute("""
				CREATE TABLE IF NOT EXISTS outreach_runs (
					run_id TEXT PRIMARY KEY,
					status TEXT NOT NULL,
					pid INTEGER,
					csv_path TEXT,
					started_at TEXT,
					finished_at TEXT,
					campaign_id TEXT,
					campaign_title TEXT,
					total_leads INTEGER DEFAULT 0,
					processed_leads INTEGER DEFAULT 0,
					duplicates_skipped INTEGER DEFAULT 0,
					resume_skipped_leads INTEGER DEFAULT 0,
					social_skipped_leads INTEGER DEFAULT 0,
					resumed_from_run_id TEXT,
					exit_code INTEGER
				)
			""")
			# Logs table
			cur.execute("""
				CREATE TABLE IF NOT EXISTS outreach_logs (
					id SERIAL PRIMARY KEY,
					run_id TEXT NOT NULL,
					line TEXT NOT NULL,
					created_at TEXT NOT NULL
				)
			""")
			# Migration: add fields_filled_data to campaign_contacts if missing
			cur.execute("""
				DO $$ BEGIN
					IF NOT EXISTS (
						SELECT 1 FROM information_schema.columns
						WHERE table_name = 'campaign_contacts' AND column_name = 'fields_filled_data'
					) THEN
						ALTER TABLE campaign_contacts ADD COLUMN fields_filled_data TEXT DEFAULT '';
					END IF;
				END $$;
			""")

			# Migration: add fields_filled_data to submission_logs if missing
			cur.execute("""
				DO $$ BEGIN
					IF NOT EXISTS (
						SELECT 1 FROM information_schema.columns
						WHERE table_name = 'submission_logs' AND column_name = 'fields_filled_data'
					) THEN
						ALTER TABLE submission_logs ADD COLUMN fields_filled_data TEXT DEFAULT '';
					END IF;
				END $$;
			""")
			cur.execute("""
				CREATE TABLE IF NOT EXISTS campaigns (
					campaign_id TEXT PRIMARY KEY,
					name TEXT NOT NULL,
					status TEXT DEFAULT 'draft',
					ai_instruction TEXT,
					max_daily_submissions INTEGER DEFAULT 100,
					search_for_form BOOLEAN DEFAULT FALSE,
					steps JSONB DEFAULT '[]',
					break_flag BOOLEAN DEFAULT FALSE,
					created_at TEXT,
					updated_at TEXT
				)
			""")
			# Contacts table
			cur.execute("""
				CREATE TABLE IF NOT EXISTS campaign_contacts (
					contact_id TEXT PRIMARY KEY,
					campaign_id TEXT NOT NULL,
					company_name TEXT,
					contact_url TEXT NOT NULL,
					domain TEXT,
					location TEXT,
					industry TEXT,
					notes TEXT,
					is_interested BOOLEAN DEFAULT FALSE,
					reply_status TEXT DEFAULT '',
					url_key TEXT NOT NULL,
					created_at TEXT,
					updated_at TEXT,
				UNIQUE (campaign_id, url_key)
				)
			""")
			# Users table
			cur.execute("""
				CREATE TABLE IF NOT EXISTS users (
					id SERIAL PRIMARY KEY,
					email TEXT UNIQUE NOT NULL,
					name TEXT,
					hashed_password TEXT NOT NULL,
					created_at TEXT,
					is_admin BOOLEAN DEFAULT FALSE
				)
			""")
			# Migration: add is_admin to users if missing
			cur.execute("""
				DO $$ BEGIN
					IF NOT EXISTS (
						SELECT 1 FROM information_schema.columns
						WHERE table_name = 'users' AND column_name = 'is_admin'
					) THEN
						ALTER TABLE users ADD COLUMN is_admin BOOLEAN DEFAULT FALSE;
					END IF;
				END $$;
			""")
			# Migration: add user_id column to campaigns if missing
			cur.execute("""
				DO $$ BEGIN
					IF NOT EXISTS (
						SELECT 1 FROM information_schema.columns
						WHERE table_name = 'campaigns' AND column_name = 'user_id'
					) THEN
						ALTER TABLE campaigns ADD COLUMN user_id INTEGER;
					END IF;
				END $$;
			""")
			# Migration: add user_id column to outreach_runs if missing
			cur.execute("""
				DO $$ BEGIN
					IF NOT EXISTS (
						SELECT 1 FROM information_schema.columns
						WHERE table_name = 'outreach_runs' AND column_name = 'user_id'
					) THEN
						ALTER TABLE outreach_runs ADD COLUMN user_id INTEGER;
					END IF;
				END $$;
			""")
			# Migration: add user_id column to campaign_contacts if missing
			cur.execute("""
				DO $$ BEGIN
					IF NOT EXISTS (
						SELECT 1 FROM information_schema.columns
						WHERE table_name = 'campaign_contacts' AND column_name = 'user_id'
					) THEN
						ALTER TABLE campaign_contacts ADD COLUMN user_id INTEGER;
					END IF;
				END $$;
			""")

			# Migration: add url_key column to campaign_contacts if missing
			cur.execute("""
				DO $$ BEGIN
					IF NOT EXISTS (
						SELECT 1 FROM information_schema.columns
						WHERE table_name = 'campaign_contacts' AND column_name = 'url_key'
					) THEN
						ALTER TABLE campaign_contacts ADD COLUMN url_key TEXT DEFAULT '';
						UPDATE campaign_contacts SET url_key = md5(contact_url) WHERE url_key = '';
					END IF;
				END $$;
			""")
			# Migration: add unique constraint for campaign_id and url_key if missing
			cur.execute("""
				DO $$ BEGIN
					IF NOT EXISTS (
						SELECT 1 FROM pg_constraint WHERE conname = 'campaign_contacts_campaign_id_url_key_key'
					) THEN
						ALTER TABLE campaign_contacts ADD CONSTRAINT campaign_contacts_campaign_id_url_key_key UNIQUE (campaign_id, url_key);
					END IF;
				END $$;
			""")
			# Migration: add schedule_day column to campaigns if missing
			cur.execute("""
				DO $$ BEGIN
					IF NOT EXISTS (
						SELECT 1 FROM information_schema.columns
						WHERE table_name = 'campaigns' AND column_name = 'schedule_day'
					) THEN
						ALTER TABLE campaigns ADD COLUMN schedule_day TEXT;
					END IF;
				END $$;
			""")
			# Migration: add schedule_time column to campaigns if missing
			cur.execute("""
				DO $$ BEGIN
					IF NOT EXISTS (
						SELECT 1 FROM information_schema.columns
						WHERE table_name = 'campaigns' AND column_name = 'schedule_time'
					) THEN
						ALTER TABLE campaigns ADD COLUMN schedule_time TEXT;
					END IF;
				END $$;
			""")
			# Contact Lists table
			cur.execute("""
				CREATE TABLE IF NOT EXISTS contact_lists (
					list_id TEXT PRIMARY KEY,
					name TEXT NOT NULL,
					user_id INTEGER,
					created_at TEXT,
					updated_at TEXT
				)
			""")
			# Contact List Items table
			cur.execute("""
				CREATE TABLE IF NOT EXISTS contact_list_items (
					id SERIAL PRIMARY KEY,
					list_id TEXT NOT NULL REFERENCES contact_lists(list_id) ON DELETE CASCADE,
					company_name TEXT,
					contact_url TEXT NOT NULL
				)
			""")
			
			# ENSURE AT LEAST ONE ADMIN EXISTS
			# For migration purposes: if any user exists, make the FIRST user an admin
			cur.execute("UPDATE users SET is_admin = TRUE WHERE id = (SELECT id FROM users ORDER BY created_at ASC LIMIT 1)")
			
			# Outreach Results table
			cur.execute("""
				CREATE TABLE IF NOT EXISTS outreach_results (
					id SERIAL PRIMARY KEY,
					campaign_id TEXT,
					run_id TEXT,
					user_id INTEGER,
					company_name TEXT,
					contact_url TEXT,
					status TEXT,
					submitted TEXT,
					confirmation_msg TEXT,
					created_at TEXT
				)
			""")

			# ASSIGN ORPHANED DATA TO THE MAIN ADMIN
			cur.execute("""
				DO $$
				DECLARE
					admin_id INTEGER;
				BEGIN
					SELECT id INTO admin_id FROM users WHERE is_admin = TRUE LIMIT 1;
					IF admin_id IS NOT NULL THEN
						UPDATE campaigns SET user_id = admin_id WHERE user_id IS NULL;
						UPDATE outreach_runs SET user_id = admin_id WHERE user_id IS NULL;
						UPDATE campaign_contacts SET user_id = admin_id WHERE user_id IS NULL;
						UPDATE contact_lists SET user_id = admin_id WHERE user_id IS NULL;
					END IF;
				END $$;
			""")

			# ── Migration: Add enriched columns to outreach_results ──
			cur.execute("ALTER TABLE outreach_results ADD COLUMN IF NOT EXISTS form_found BOOLEAN DEFAULT FALSE")
			cur.execute("ALTER TABLE outreach_results ADD COLUMN IF NOT EXISTS captcha_present BOOLEAN DEFAULT FALSE")
			cur.execute("ALTER TABLE outreach_results ADD COLUMN IF NOT EXISTS captcha_type TEXT DEFAULT 'none'")
			cur.execute("ALTER TABLE outreach_results ADD COLUMN IF NOT EXISTS captcha_result TEXT DEFAULT ''")
			cur.execute("ALTER TABLE outreach_results ADD COLUMN IF NOT EXISTS http_status_code INTEGER")
			cur.execute("ALTER TABLE outreach_results ADD COLUMN IF NOT EXISTS error_detail TEXT DEFAULT ''")
			cur.execute("ALTER TABLE outreach_results ADD COLUMN IF NOT EXISTS bandwidth_kb NUMERIC(10,2) DEFAULT 0")
			cur.execute("ALTER TABLE outreach_results ADD COLUMN IF NOT EXISTS domain TEXT DEFAULT ''")

			# Global submitted contacts table for cross-campaign deduplication
			cur.execute("""
				CREATE TABLE IF NOT EXISTS global_submitted_contacts (
					id SERIAL PRIMARY KEY,
					contact_url TEXT UNIQUE NOT NULL,
					domain TEXT DEFAULT '',
					first_submitted_at TEXT,
					first_campaign_id TEXT,
					first_run_id TEXT,
					submission_count INTEGER DEFAULT 1
				)
			""")

			# ── MIGRATION: NORMALIZED DATABASE ARCHITECTURE ──

			# ENUMs
			cur.execute("""
				DO $$ BEGIN
					CREATE TYPE campaign_status AS ENUM ('pending', 'processing', 'completed');
				EXCEPTION WHEN duplicate_object THEN null;
				END $$;
			""")
			cur.execute("""
				DO $$ BEGIN
					CREATE TYPE attempt_status AS ENUM ('pending', 'submitted', 'failed', 'skipped');
				EXCEPTION WHEN duplicate_object THEN null;
				END $$;
			""")
			cur.execute("""
				DO $$ BEGIN
					CREATE TYPE attempt_detail_status AS ENUM (
						'successful',
						'submission_not_confirmed',
						'form_validation_failed',
						'submit_button_not_found',
						'website_error',
						'invalid_field_value',
						'http_403',
						'http_404',
						'rate_limited',
						'http_423',
						'cloudflare_challenge',
						'recaptcha2_timeout',
						'recaptcha2_no_sitekey',
						'recaptcha3_timeout',
						'recaptcha3_no_sitekey',
						'turnstile_timeout',
						'turnstile_no_sitekey',
						'hcaptcha_timeout',
						'hcaptcha_no_sitekey'
					);
				EXCEPTION WHEN duplicate_object THEN null;
				END $$;
			""")

			# 1. Contacts (Master) Table
			cur.execute("""
				CREATE TABLE IF NOT EXISTS contacts (
					id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
					name TEXT,
					email TEXT,
					phone TEXT,
					source TEXT,
					created_at TIMESTAMP DEFAULT NOW()
				)
			""")
			cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS unique_contact_identity ON contacts (LOWER(email), phone)")

			# 2. Campaigns Table updates
			cur.execute("""
				DO $$ BEGIN
					IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'campaigns' AND column_name = 'started_at') THEN
						ALTER TABLE campaigns ADD COLUMN started_at TIMESTAMP;
					END IF;
					IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'campaigns' AND column_name = 'ended_at') THEN
						ALTER TABLE campaigns ADD COLUMN ended_at TIMESTAMP;
					END IF;
				END $$;
			""")

			# 3. Campaign Contacts Table updates
			cur.execute("""
				DO $$ BEGIN
					IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'campaign_contacts' AND column_name = 'attempt_status') THEN
						ALTER TABLE campaign_contacts ADD COLUMN attempt_status attempt_status DEFAULT 'pending';
					END IF;
					IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'campaign_contacts' AND column_name = 'attempt_detail_status') THEN
						ALTER TABLE campaign_contacts ADD COLUMN attempt_detail_status attempt_detail_status;
					END IF;
					IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'campaign_contacts' AND column_name = 'form_present') THEN
						ALTER TABLE campaign_contacts ADD COLUMN form_present BOOLEAN;
					END IF;
					IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'campaign_contacts' AND column_name = 'captcha_present') THEN
						ALTER TABLE campaign_contacts ADD COLUMN captcha_present BOOLEAN;
					END IF;
					IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'campaign_contacts' AND column_name = 'proxy_bandwidth_mb') THEN
						ALTER TABLE campaign_contacts ADD COLUMN proxy_bandwidth_mb NUMERIC DEFAULT 0;
					END IF;
					IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'campaign_contacts' AND column_name = 'submitted_at') THEN
						ALTER TABLE campaign_contacts ADD COLUMN submitted_at TIMESTAMP;
					END IF;
					IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'campaign_contacts' AND column_name = 'skipped_reason') THEN
						ALTER TABLE campaign_contacts ADD COLUMN skipped_reason TEXT;
					END IF;
				END $$;
			""")

			# 4. Attempt Steps Table
			cur.execute("""
				CREATE TABLE IF NOT EXISTS attempt_steps (
					id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
					campaign_contact_id TEXT NOT NULL REFERENCES campaign_contacts(contact_id) ON DELETE CASCADE,
					step_name TEXT,
					step_order INT,
					step_status TEXT,
					detail_status attempt_detail_status,
					message TEXT,
					created_at TIMESTAMP DEFAULT NOW()
				)
			""")

			# 5. Submission Logs Table
			cur.execute("""
				CREATE TABLE IF NOT EXISTS submission_logs (
					id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
					contact_id UUID REFERENCES contacts(id) ON DELETE CASCADE,
					campaign_id TEXT REFERENCES campaigns(campaign_id) ON DELETE CASCADE,
					campaign_contact_id TEXT REFERENCES campaign_contacts(contact_id) ON DELETE CASCADE,
					status attempt_status,
					detail_status attempt_detail_status,
					request_payload JSONB,
					response_payload JSONB,
					message TEXT,
					created_at TIMESTAMP DEFAULT NOW()
				)
			""")

			# 6. Global Submission Lock Table
			cur.execute("""
				CREATE TABLE IF NOT EXISTS global_submission_lock (
					contact_id UUID PRIMARY KEY REFERENCES contacts(id) ON DELETE CASCADE,
					first_campaign_id TEXT REFERENCES campaigns(campaign_id),
					status attempt_detail_status,
					locked_at TIMESTAMP DEFAULT NOW()
				)
			""")

			# 7. Performance indexes 
			cur.execute("CREATE INDEX IF NOT EXISTS idx_outreach_results_run_id ON outreach_results(run_id)")
			cur.execute("CREATE INDEX IF NOT EXISTS idx_outreach_results_campaign_id ON outreach_results(campaign_id)")
			cur.execute("CREATE INDEX IF NOT EXISTS idx_outreach_results_status ON outreach_results(status, captcha_present)")
			cur.execute("CREATE INDEX IF NOT EXISTS idx_outreach_results_contact_url ON outreach_results(contact_url)")
			cur.execute("CREATE INDEX IF NOT EXISTS idx_outreach_logs_run_id ON outreach_logs(run_id)")
			cur.execute("CREATE INDEX IF NOT EXISTS idx_campaign_contacts_campaign_id ON campaign_contacts(campaign_id)")
			cur.execute("CREATE INDEX IF NOT EXISTS idx_outreach_runs_campaign_id ON outreach_runs(campaign_id)")
			cur.execute("CREATE INDEX IF NOT EXISTS idx_global_submitted_url ON global_submitted_contacts(contact_url)")
			
			# New Normalized Indexes
			cur.execute("CREATE INDEX IF NOT EXISTS idx_new_campaign_contacts_status ON campaign_contacts(attempt_detail_status)")
			cur.execute("CREATE INDEX IF NOT EXISTS idx_new_attempt_steps_cc ON attempt_steps(campaign_contact_id)")
			cur.execute("CREATE INDEX IF NOT EXISTS idx_new_logs_contact ON submission_logs(contact_id)")
			cur.execute("CREATE INDEX IF NOT EXISTS idx_new_logs_campaign ON submission_logs(campaign_id)")

			# ── Migration: Step tracking for multi-step campaigns ──
			cur.execute("""
				DO $$ BEGIN
					IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'campaign_contacts' AND column_name = 'current_step_index') THEN
						ALTER TABLE campaign_contacts ADD COLUMN current_step_index INTEGER DEFAULT 0;
					END IF;
					IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'campaign_contacts' AND column_name = 'next_attempt_at') THEN
						ALTER TABLE campaign_contacts ADD COLUMN next_attempt_at TIMESTAMPTZ DEFAULT NOW();
					END IF;
				END $$;
			""")
			cur.execute("ALTER TABLE outreach_results ADD COLUMN IF NOT EXISTS step_index INTEGER DEFAULT 0")

			conn.commit()
		_db_pool.putconn(conn)
		_db_available = True
		_db_init_error = None
		print("[DB] PostgreSQL (Supabase) Initialization successful")
	except Exception as exc:
		_db_available = False
		_db_init_error = str(exc)
		print(f"[DB] PostgreSQL Initialization failed: {exc}")


def _db_get_conn():
	if not _db_available or _db_pool is None:
		raise HTTPException(status_code=503, detail="Database is not connected")
	return _db_pool.getconn()

def _db_put_conn(conn):
	if _db_pool:
		_db_pool.putconn(conn)


def _db_record_run_start(
	run_id: str,
	pid: int,
	csv_path: str | None,
	started_at: str,
	*,
	campaign_id: str | None,
	campaign_title: str | None,
	total_leads: int,
	duplicates_skipped: int,
	resume_skipped_leads: int,
	social_skipped_leads: int,
	resumed_from_run_id: str | None,
	user_id: str | None = None,
) -> None:
	if not _db_available or _db_pool is None:
		return
	conn = _db_get_conn()
	try:
		with conn.cursor() as cur:
			cur.execute("""
				INSERT INTO outreach_runs (
					run_id, status, pid, csv_path, started_at, campaign_id, campaign_title,
					total_leads, processed_leads, duplicates_skipped, resume_skipped_leads,
					social_skipped_leads, resumed_from_run_id, user_id
				) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
				ON CONFLICT (run_id) DO UPDATE SET
					status = EXCLUDED.status,
					pid = EXCLUDED.pid,
					csv_path = EXCLUDED.csv_path,
					started_at = EXCLUDED.started_at,
					campaign_id = EXCLUDED.campaign_id,
					campaign_title = EXCLUDED.campaign_title,
					total_leads = EXCLUDED.total_leads,
					processed_leads = EXCLUDED.processed_leads,
					duplicates_skipped = EXCLUDED.duplicates_skipped,
					resume_skipped_leads = EXCLUDED.resume_skipped_leads,
					social_skipped_leads = EXCLUDED.social_skipped_leads,
					resumed_from_run_id = EXCLUDED.resumed_from_run_id,
					user_id = EXCLUDED.user_id
			""", (
				run_id, "running", pid, csv_path, started_at, campaign_id, campaign_title,
				int(total_leads), 0, int(duplicates_skipped), int(resume_skipped_leads),
				int(social_skipped_leads), resumed_from_run_id, user_id
			))
			conn.commit()
	except Exception as exc:
		print(f"[DB] Failed to record run start: {exc}")
	finally:
		_db_put_conn(conn)


def _db_update_run_state(
	run_id: str | None,
	*,
	status: str,
	finished_at: str | None = None,
	exit_code: int | None = None,
	processed_leads: int | None = None,
	total_leads: int | None = None,
	duplicates_skipped: int | None = None,
	resume_skipped_leads: int | None = None,
	social_skipped_leads: int | None = None,
	resumed_from_run_id: str | None = None,
) -> None:
	if not _db_available or not run_id or _db_pool is None:
		return
	conn = _db_get_conn()
	try:
		updates: list[str] = ["status = %s"]
		params: list[Any] = [status]
		
		if finished_at is not None:
			updates.append("finished_at = %s")
			params.append(finished_at)
		if exit_code is not None:
			updates.append("exit_code = %s")
			params.append(int(exit_code))
		if processed_leads is not None:
			updates.append("processed_leads = %s")
			params.append(max(0, int(processed_leads)))
		if total_leads is not None:
			updates.append("total_leads = %s")
			params.append(max(0, int(total_leads)))
		if duplicates_skipped is not None:
			updates.append("duplicates_skipped = %s")
			params.append(max(0, int(duplicates_skipped)))
		if resume_skipped_leads is not None:
			updates.append("resume_skipped_leads = %s")
			params.append(max(0, int(resume_skipped_leads)))
		if social_skipped_leads is not None:
			updates.append("social_skipped_leads = %s")
			params.append(max(0, int(social_skipped_leads)))
		if resumed_from_run_id is not None:
			updates.append("resumed_from_run_id = %s")
			params.append(_safe_trim(resumed_from_run_id))

		params.append(run_id)
		sql = f"UPDATE outreach_runs SET {', '.join(updates)} WHERE run_id = %s"
		
		with conn.cursor() as cur:
			cur.execute(sql, tuple(params))
			conn.commit()
	except Exception as exc:
		print(f"[DB] Failed to update run state: {exc}")
	finally:
		_db_put_conn(conn)


def _db_append_log(run_id: str | None, line: str) -> None:
	if not _db_available or not run_id or _db_pool is None:
		return
	conn = _db_get_conn()
	try:
		with conn.cursor() as cur:
			cur.execute("""
				INSERT INTO outreach_logs (run_id, line, created_at)
				VALUES (%s, %s, %s)
			""", (run_id, line, _utc_now_iso()))
			conn.commit()
	except Exception as exc:
		print(f"[DB] Failed to append log: {exc}")
	finally:
		_db_put_conn(conn)


def _db_record_result(run_id: str | None, campaign_id: str | None, user_id: str | None, parsed_result: dict[str, Any]) -> None:
	if not _db_available or not run_id or _db_pool is None:
		return
	conn = _db_get_conn()
	try:
		with conn.cursor() as cur:
			# 1. Old (legacy) tracking table
			cur.execute("""
				INSERT INTO outreach_results (
					campaign_id, run_id, user_id, company_name, contact_url,
					status, submitted, confirmation_msg,
					form_found, captcha_present, captcha_type, captcha_result,
					http_status_code, error_detail, bandwidth_kb, domain,
					created_at, fields_filled_data
				) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
			""", (
				campaign_id,
				run_id,
				user_id,
				parsed_result.get("companyName", ""),
				parsed_result.get("contactUrl", ""),
				parsed_result.get("status", ""),
				parsed_result.get("submitted", ""),
				parsed_result.get("confirmationMsg", ""),
				parsed_result.get("formFound", False),
				parsed_result.get("captchaPresent", False),
				parsed_result.get("captchaType", "none"),
				parsed_result.get("captchaResult", ""),
				parsed_result.get("httpStatusCode"),
				parsed_result.get("errorDetail", ""),
				parsed_result.get("bandwidthKb", 0),
				parsed_result.get("domain", ""),
				_utc_now_iso(),
				parsed_result.get("fieldsFilled", "")
			))

			is_submitted = str(parsed_result.get("submitted") or "").strip().lower() == "yes"
			attempt_status_val = "submitted" if is_submitted else "failed"

			legacy_status = str(parsed_result.get("status") or "")
			captcha_present = bool(parsed_result.get("captchaPresent", False))
			captcha_type = str(parsed_result.get("captchaType", ""))
			captcha_result = str(parsed_result.get("captchaResult", ""))
			http_code = parsed_result.get("httpStatusCode")
			
			detail_status = "successful" if is_submitted else None
			
			if not is_submitted:
				if captcha_present:
					if captcha_type == "cloudflare": detail_status = "cloudflare_challenge"
					elif captcha_type == "recaptcha2" and captcha_result == "timeout": detail_status = "recaptcha2_timeout"
					elif captcha_type == "recaptcha2" and captcha_result == "no_sitekey": detail_status = "recaptcha2_no_sitekey"
					elif captcha_type == "recaptcha3" and captcha_result == "timeout": detail_status = "recaptcha3_timeout"
					elif captcha_type == "recaptcha3" and captcha_result == "no_sitekey": detail_status = "recaptcha3_no_sitekey"
					elif captcha_type == "turnstile" and captcha_result == "timeout": detail_status = "turnstile_timeout"
					elif captcha_type == "turnstile" and captcha_result == "no_sitekey": detail_status = "turnstile_no_sitekey"
					elif captcha_type == "hcaptcha" and captcha_result == "timeout": detail_status = "hcaptcha_timeout"
					elif captcha_type == "hcaptcha" and captcha_result == "no_sitekey": detail_status = "hcaptcha_no_sitekey"
					elif legacy_status == "invalid_field_value": detail_status = "invalid_field_value"
					else: detail_status = "website_error"
				else:
					if legacy_status == "submission_not_confirmed": detail_status = "submission_not_confirmed"
					elif legacy_status == "form_validation_failed": detail_status = "form_validation_failed"
					elif legacy_status == "submit_button_not_found": detail_status = "submit_button_not_found"
					elif legacy_status == "invalid_field_value": detail_status = "invalid_field_value"
					elif legacy_status == "rate_limited": detail_status = "rate_limited"
					elif http_code == 403: detail_status = "http_403"
					elif http_code == 404: detail_status = "http_404"
					elif http_code == 423: detail_status = "http_423"
					else: detail_status = "website_error"

			# Create dynamic contact ID to satisfy constraints since the migration is partial
			contact_url = str(parsed_result.get("contactUrl") or "").strip()
			url_key = _normalize_url_key(contact_url)


			# Attempt to update campaign_contacts with new columns, including fields_filled_data
			fields_filled_data = parsed_result.get("fieldsFilled", "")
			if campaign_id and url_key:
				cur.execute("""
					UPDATE campaign_contacts
					SET attempt_status = %s::attempt_status,
						attempt_detail_status = %s::attempt_detail_status,
						form_present = %s,
						captcha_present = %s,
						proxy_bandwidth_mb = %s,
						submitted_at = %s,
						fields_filled_data = %s
					WHERE campaign_id = %s AND url_key = %s
					RETURNING contact_id
				""", (
					attempt_status_val,
					detail_status,
					parsed_result.get("formFound", False),
					captcha_present,
					float(parsed_result.get("bandwidthKb", 0)) / 1024.0,
					_utc_now_iso(),
					fields_filled_data,
					campaign_id,
					url_key
				))
				cc_row = cur.fetchone()
				cc_id = cc_row[0] if cc_row else None

				if cc_id:
					# Generate a dummy contacts entry because the schema demands contact_id UUID
					# In a fully migrated system, this would be looked up by email/phone
					cur.execute("INSERT INTO contacts (name, source) VALUES (%s, 'migration_shim') RETURNING id", (parsed_result.get("companyName", ""),))
					master_c_id = cur.fetchone()[0]

					raw_payload = parsed_result.get("_raw_payload", {})

					cur.execute("""
						INSERT INTO submission_logs (contact_id, campaign_id, campaign_contact_id, status, detail_status, request_payload, fields_filled_data)
						VALUES (%s, %s, %s, %s::attempt_status, %s::attempt_detail_status, %s, %s)
					""", (master_c_id, campaign_id, cc_id, attempt_status_val, detail_status, json.dumps(raw_payload), fields_filled_data))

					if is_submitted:
						cur.execute("""
							INSERT INTO global_submission_lock (contact_id, first_campaign_id, status)
							VALUES (%s, %s, %s::attempt_detail_status)
						""", (master_c_id, campaign_id, detail_status))

			# Upsert into old global_submitted_contacts when submitted == "Yes" (keeping old dedup intact)
			if is_submitted and contact_url:
				cur.execute("""
					INSERT INTO global_submitted_contacts (
						contact_url, domain, first_submitted_at, first_campaign_id, first_run_id, submission_count
					) VALUES (%s, %s, %s, %s, %s, 1)
					ON CONFLICT (contact_url) DO UPDATE SET
						submission_count = global_submitted_contacts.submission_count + 1
				""", (
					contact_url,
					str(parsed_result.get("domain") or ""),
					_utc_now_iso(),
					campaign_id or "",
					run_id or "",
				))
			conn.commit()
	except Exception as exc:
		print(f"[DB] Failed to record result: {exc}")
	finally:
		_db_put_conn(conn)


def _db_get_latest_run(user_id: str | None = None, is_admin: bool = False) -> dict[str, Any] | None:
	if not _db_available or _db_pool is None:
		return None
	conn = _db_get_conn()
	try:
		with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
			if is_admin:
				cur.execute("SELECT * FROM outreach_runs ORDER BY started_at DESC LIMIT 1")
			else:
				if not user_id: return None
				cur.execute("SELECT * FROM outreach_runs WHERE user_id = %s ORDER BY started_at DESC LIMIT 1", (user_id,))
			doc = cur.fetchone()
			if not doc:
				return None
			return dict(doc)
	except Exception:
		return None
	finally:
		_db_put_conn(conn)


def _db_get_run(run_id: str, user_id: str | None = None, is_admin: bool = False) -> dict[str, Any] | None:
	if not _db_available or _db_pool is None:
		return None
	conn = _db_get_conn()
	try:
		with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
			if is_admin:
				cur.execute("""
					SELECT * FROM outreach_runs WHERE run_id = %s
				""", (run_id,))
			else:
				if not user_id: return None
				cur.execute("""
					SELECT * FROM outreach_runs WHERE run_id = %s AND user_id = %s
				""", (run_id, user_id))
			doc = cur.fetchone()
			if not doc:
				return None
			return dict(doc)
	except Exception:
		return None
	finally:
		_db_put_conn(conn)


def _db_get_latest_resumable_run(campaign_id: str, user_id: str | None = None) -> dict[str, Any] | None:
	if not _db_available or _db_pool is None or not campaign_id:
		return None
	conn = _db_get_conn()
	try:
		with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
			if user_id:
				cur.execute("""
					SELECT run_id, campaign_id, status, started_at
					FROM outreach_runs
					WHERE campaign_id = %s AND user_id = %s AND status NOT IN ('running', 'stopping', 'queued')
					ORDER BY started_at DESC LIMIT 1
				""", (campaign_id, user_id))
			else:
				cur.execute("""
					SELECT run_id, campaign_id, status, started_at
					FROM outreach_runs
					WHERE campaign_id = %s AND status NOT IN ('running', 'stopping', 'queued')
					ORDER BY started_at DESC LIMIT 1
				""", (campaign_id,))
			doc = cur.fetchone()
			if not doc:
				return None
			return dict(doc)
	except Exception:
		return None
	finally:
		_db_put_conn(conn)


def _db_get_latest_resumable_run_any(user_id: str | None = None) -> dict[str, Any] | None:
	if not _db_available or _db_pool is None:
		return None
	conn = _db_get_conn()
	try:
		with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
			if user_id:
				cur.execute("""
					SELECT run_id, campaign_id, status, started_at
					FROM outreach_runs
					WHERE user_id = %s AND status NOT IN ('running', 'stopping', 'queued')
					ORDER BY started_at DESC LIMIT 1
				""", (user_id,))
			else:
				cur.execute("""
					SELECT run_id, campaign_id, status, started_at
					FROM outreach_runs
					WHERE status NOT IN ('running', 'stopping', 'queued')
					ORDER BY started_at DESC LIMIT 1
				""")
			doc = cur.fetchone()
			if not doc:
				return None
			return dict(doc)
	except Exception:
		return None
	finally:
		_db_put_conn(conn)


def _db_get_processed_url_keys(run_id: str) -> set[str]:
	if not _db_available or _db_pool is None or not run_id:
		return set()

	keys: set[str] = set()
	conn = _db_get_conn()
	try:
		with conn.cursor() as cur:
			cur.execute("""
				SELECT line FROM outreach_logs
				WHERE run_id = %s ORDER BY created_at ASC
			""", (run_id,))
			for (line,) in cur:
				parsed = _parse_result_line(str(line or ""))
				if parsed is None:
					continue
				url_key = _normalize_url_key(str(parsed.get("contactUrl") or ""))
				if url_key:
					keys.add(url_key)
	except Exception:
		return set()
	finally:
		_db_put_conn(conn)

	return keys


def _db_get_logs(run_id: str, tail: int) -> list[str]:
	if not _db_available or _db_pool is None:
		return []
	conn = _db_get_conn()
	try:
		with conn.cursor() as cur:
			cur.execute("""
				SELECT line FROM outreach_logs
				WHERE run_id = %s ORDER BY created_at DESC LIMIT %s
			""", (run_id, int(tail)))
			rows = [str(row[0] or "") for row in cur]
			return [line for line in reversed(rows) if line]
	except Exception:
		return []
	finally:
		_db_put_conn(conn)


def _db_count_campaign_successes_today(campaign_id: str) -> int:
	"""Count successful submissions for a specific campaign in the current UTC day."""
	if not _db_available or _db_pool is None or not campaign_id:
		return 0
	
	now_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
	conn = _db_get_conn()
	try:
		with conn.cursor() as cur:
			# Successful submissions are recorded as [RESULT] payloads in outreach_logs
			# where "submitted": "Yes"
			# We filter by run_id belonging to this campaign and created_at starting with today's date
			cur.execute("""
				SELECT COUNT(*) 
				FROM outreach_logs l
				JOIN outreach_runs r ON l.run_id = r.run_id
				WHERE r.campaign_id = %s 
				  AND l.created_at LIKE %s 
				  AND l.line LIKE '%%[RESULT]%%"submitted": "Yes"%%'
			""", (campaign_id, f"{now_date}%"))
			count = cur.fetchone()[0]
			return int(count or 0)
	except Exception as exc:
		print(f"[DB] Failed to count successes today: {exc}")
		return 0
	finally:
		_db_put_conn(conn)


def _materialize_google_credentials_file() -> None:
	raw = str(os.environ.get("GOOGLE_CREDENTIALS_JSON", "") or "").strip()
	if not raw:
		return

	creds_path = BASE_DIR / "google_credentials.json"
	if creds_path.exists():
		return

	try:
		parsed = json.loads(raw)
		creds_path.write_text(json.dumps(parsed), encoding="utf-8")
	except Exception:
		try:
			creds_path.write_text(raw, encoding="utf-8")
		except Exception:
			pass


_materialize_google_credentials_file()
_init_db()

app = FastAPI(
	title="Outreach FastAPI Backend",
	version="1.0.0",
	description="API endpoints to start and monitor Outreach(1).py runs.",
)

_state_lock = threading.Lock()
# Dict of run_id -> run state dict, supports multiple concurrent campaigns
_active_runs: dict[str, dict[str, Any]] = {}
MAX_CONCURRENT_RUNS = 3  # limit to protect 4GB RAM


def _new_run_state() -> dict[str, Any]:
	"""Create a fresh run state dictionary."""
	return {
		"process": None,
		"reader_thread": None,
		"started_at": None,
		"finished_at": None,
		"exit_code": None,
		"csv_path": None,
		"logs": deque(maxlen=LOG_BUFFER_SIZE),
		"total_leads": 0,
		"processed_leads": 0,
		"current_lead": "-",
		"results": [],
		"duplicates_skipped": 0,
		"generated_csv_path": None,
		"campaign_id": None,
		"campaign_title": None,
		"user_id": None,
		"resume_skipped_leads": 0,
		"social_skipped_leads": 0,
		"resumed_from_run_id": None,
	}


def _count_running() -> int:
	"""Count how many runs are currently alive (must hold _state_lock)."""
	return sum(1 for s in _active_runs.values() if s["process"] is not None and (s["process"].poll() is None or (s.get("reader_thread") and s["reader_thread"].is_alive())))


class OutreachStartRequest(BaseModel):
	csv_path: str | None = Field(
		default=None,
		description="Optional CSV path. Relative paths are resolved from project root.",
	)
	leads: list[dict[str, Any]] | None = Field(
		default=None,
		description="Optional leads payload. If provided, backend builds a run CSV automatically.",
	)
	persona: dict[str, Any] | None = Field(
		default=None,
		description="Optional persona payload used to set runtime environment values.",
	)
	resume: bool = Field(
		default=True,
		description="Resume from the latest non-running run for this campaign when possible.",
	)
	resume_from_run_id: str | None = Field(
		default=None,
		description="Optional specific run_id to use as resume bookmark.",
	)
	dedupe_by_domain: bool = Field(
		default=True,
		description="If true, keep only a limited number of URLs per domain while building run CSV.",
	)
	max_urls_per_domain: int = Field(
		default=1,
		ge=1,
		le=20,
		description="Maximum number of URLs to keep per domain when dedupe_by_domain is enabled.",
	)


class CampaignCreateRequest(BaseModel):
	name: str = Field(min_length=1, max_length=140)
	aiInstruction: str = Field(default="", max_length=30000)
	status: str = Field(default="draft", max_length=20)
	maxDailySubmissions: int = Field(default=100, ge=1, le=100000)
	searchForForm: bool = Field(default=False)
	breakFlag: bool = Field(default=False)
	steps: list[Any] = Field(default_factory=list)


class CampaignUpdateRequest(BaseModel):
	name: str | None = Field(default=None, min_length=1, max_length=140)
	aiInstruction: str | None = Field(default=None, max_length=30000)
	status: str | None = Field(default=None, max_length=20)
	maxDailySubmissions: int | None = Field(default=None, ge=1, le=100000)
	searchForForm: bool | None = Field(default=None)
	breakFlag: bool | None = Field(default=None)
	steps: list[Any] | None = Field(default=None)
	scheduleDay: str | None = Field(default=None, max_length=20)
	scheduleTime: str | None = Field(default=None, max_length=10)


class CampaignContactCreateRequest(BaseModel):
	companyName: str = Field(min_length=1, max_length=200)
	contactUrl: str = Field(min_length=1, max_length=2000)
	location: str | None = Field(default=None, max_length=180)
	industry: str | None = Field(default=None, max_length=180)
	notes: str | None = Field(default=None, max_length=2000)


class BulkContactsCreateRequest(BaseModel):
	contacts: list[dict[str, Any]]
	force: bool = Field(default=False)


def _safe_trim(value: Any) -> str:
	return str(value or "").strip()


def _normalize_campaign_status(raw_status: str) -> str:
	value = _safe_trim(raw_status).lower()
	allowed = {"draft", "active", "paused", "archived"}
	return value if value in allowed else "draft"


def _is_social_domain(domain: str) -> bool:
	host = _safe_trim(domain).lower().replace("www.", "", 1)
	if not host:
		return False
	return any(host == blocked or host.endswith(f".{blocked}") for blocked in SOCIAL_MEDIA_DOMAINS)


class ContactUpdateRequest(BaseModel):
	companyName: str | None = Field(default=None, min_length=1, max_length=200)
	isInterested: bool | None = Field(default=None)
	replyStatus: str | None = Field(default=None)


def _build_search_filter_sql(search_text: str | None, fields: list[str]) -> tuple[str, list[str]]:
	query_text = _safe_trim(search_text)
	if not query_text:
		return "1=1", []
	
	clauses = " OR ".join([f"{field} ILIKE %s" for field in fields])
	params = [f"%{query_text}%"] * len(fields)
	return f"({clauses})", params


def _build_pagination_meta(page: int, limit: int, total: int) -> dict[str, int]:
	safe_page = max(1, int(page))
	safe_limit = max(1, int(limit))
	safe_total = max(0, int(total))
	total_pages = max(1, (safe_total + safe_limit - 1) // safe_limit) if safe_total else 1
	return {
		"page": safe_page,
		"limit": safe_limit,
		"total": safe_total,
		"total_pages": total_pages,
	}


def _normalize_contact_url(raw_url: str) -> tuple[str, str, str]:
	value = _safe_trim(raw_url).strip("\"'")
	if not value:
		raise HTTPException(status_code=422, detail="Contact URL is required")

	candidate = value if value.lower().startswith(("http://", "https://")) else f"https://{value.lstrip('/')}"
	parsed = urlparse(candidate)

	if parsed.scheme not in {"http", "https"} or not parsed.netloc:
		raise HTTPException(status_code=422, detail="Use a valid http/https URL")

	host = (parsed.hostname or "").replace("www.", "", 1).lower()
	if not host:
		raise HTTPException(status_code=422, detail="Unable to resolve URL domain")
	if _is_social_domain(host):
		raise HTTPException(status_code=422, detail=SOCIAL_URL_DETAIL)

	path_name = parsed.path.rstrip("/") or "/"
	query = f"?{parsed.query}" if parsed.query else ""
	normalized_url = f"{parsed.scheme}://{parsed.netloc.lower()}{path_name}{query}"
	url_key = f"{host}{path_name}"
	return normalized_url, host, url_key


def _normalize_contact_url_lenient(raw_url: str) -> tuple[str, str, str] | None:
	"""Lenient URL normalizer for bulk imports — accepts social media URLs
	and uses full path+query for url_key to avoid over-deduplication."""
	value = _safe_trim(raw_url).strip("\"'")
	if not value:
		return None

	candidate = value if value.lower().startswith(("http://", "https://")) else f"https://{value.lstrip('/')}"
	parsed = urlparse(candidate)

	if parsed.scheme not in {"http", "https"} or not parsed.netloc:
		return None

	host = (parsed.hostname or "").replace("www.", "", 1).lower()
	if not host:
		return None

	path_name = parsed.path.rstrip("/") or "/"
	query = f"?{parsed.query}" if parsed.query else ""
	normalized_url = f"{parsed.scheme}://{parsed.netloc.lower()}{path_name}{query}"
	# Use full path+query in url_key so different pages on same domain are kept
	url_key = f"{host}{path_name}{query}"
	return normalized_url, host, url_key





def _map_campaign_document(
	doc: dict[str, Any],
	*,
	contact_count: int = 0,
	last_run: dict[str, Any] | None = None,
) -> dict[str, Any]:
	return {
		"id": _safe_trim(doc.get("campaign_id")),
		"name": _safe_trim(doc.get("name")),
		"status": _safe_trim(doc.get("status")) or "draft",
		"aiInstruction": _safe_trim(doc.get("ai_instruction")),
		"maxDailySubmissions": int(doc.get("max_daily_submissions") or 100),
		"searchForForm": bool(doc.get("search_for_form") or False),
		"breakFlag": bool(doc.get("break_flag") or False),
		"steps": doc.get("steps") or [],
		"scheduleDay": _safe_trim(doc.get("schedule_day")) or "monday",
		"scheduleTime": _safe_trim(doc.get("schedule_time")) or "09:00",
		"contactCount": int(contact_count),
		"createdAt": _safe_trim(doc.get("created_at")),
		"updatedAt": _safe_trim(doc.get("updated_at")),
		"lastRun": last_run,
	}


def _map_contact_document(doc: dict[str, Any]) -> dict[str, Any]:
	return {
		"id": _safe_trim(doc.get("contact_id")),
		"campaignId": _safe_trim(doc.get("campaign_id")),
		"companyName": _safe_trim(doc.get("company_name")),
		"contactUrl": _safe_trim(doc.get("contact_url")),
		"domain": _safe_trim(doc.get("domain")),
		"location": _safe_trim(doc.get("location")),
		"industry": _safe_trim(doc.get("industry")),
		"notes": _safe_trim(doc.get("notes")),
		"isInterested": bool(doc.get("is_interested") or False),
		"replyStatus": _safe_trim(doc.get("reply_status")) or "",
		"createdAt": _safe_trim(doc.get("created_at")),
		"updatedAt": _safe_trim(doc.get("updated_at")),
	}


def _campaign_last_run(campaign_id: str) -> dict[str, Any] | None:
	if not _db_available or _db_pool is None:
		return None
	conn = _db_get_conn()
	try:
		with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
			cur.execute("""
				SELECT run_id, status, started_at, finished_at, exit_code, total_leads, processed_leads, duplicates_skipped
				FROM outreach_runs
				WHERE campaign_id = %s
				ORDER BY started_at DESC LIMIT 1
			""", (campaign_id,))
			doc = cur.fetchone()
			if not doc:
				return None
			res = dict(doc)
			# Rename keys to match frontend expectations if necessary
			return {
				"runId": res.get("run_id"),
				"status": res.get("status"),
				"startedAt": res.get("started_at"),
				"finishedAt": res.get("finished_at"),
				"exitCode": res.get("exit_code"),
				"totalLeads": res.get("total_leads"),
				"processedLeads": res.get("processed_leads"),
				"duplicatesSkipped": res.get("duplicates_skipped"),
			}
	except Exception:
		return None
	finally:
		_db_put_conn(conn)


def _ensure_campaign_exists(campaign_id: str, user_id: str = "", is_admin: bool = False) -> dict[str, Any]:
	"""Raises 404 if campaign doesn't exist or isn't owned by user."""
	if not _db_available or _db_pool is None:
		raise HTTPException(status_code=503, detail="Database is not connected")
	conn = _db_get_conn()
	try:
		with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
			if is_admin:
				cur.execute("SELECT * FROM campaigns WHERE campaign_id = %s", (campaign_id,))
			else:
				cur.execute("SELECT * FROM campaigns WHERE campaign_id = %s AND user_id = %s", (campaign_id, user_id))
			doc = cur.fetchone()
			if not doc:
				raise HTTPException(status_code=404, detail=f"Campaign not found: {campaign_id}")
			return dict(doc)
	finally:
		_db_put_conn(conn)


def _utc_now_iso() -> str:
	return datetime.now(timezone.utc).isoformat()


def _parse_cost(value: Any) -> float:
	numeric = str(value or "")
	filtered = "".join(char for char in numeric if char.isdigit() or char in {".", "-"})
	try:
		return float(filtered)
	except Exception:
		return 0.0


def _status_from_result(submitted: str, captcha_status: str, submission_status: str, assurance: str) -> str:
	if str(submitted or "").strip().lower() == "yes":
		return "success"

	combined = f"{captcha_status} {submission_status} {assurance}".lower()
	if (
		"timeout" in combined
		or "captcha" in combined
		or "warning" in combined
		or "not found" in combined
	):
		return "warning"

	return "fail"


def _map_result_payload(payload: dict[str, Any]) -> dict[str, Any]:
	submitted_raw = str(payload.get("submitted") or "No")
	captcha_status = str(payload.get("captcha_status") or "n/a")
	submission_status = str(payload.get("submission_status") or "")
	assurance = str(payload.get("submission_assurance") or "")

	# Properly parse "Yes"/"No" strings from the Outreach script
	captcha_present_raw = str(payload.get("captcha_present") or "No").strip().lower()
	captcha_present = captcha_present_raw == "yes"

	# Derive form_found: the Outreach script doesn't emit this key directly.
	# If fields were filled (not "- none") AND assurance doesn't say "not found",
	# we consider a form was found.
	fields_filled = str(payload.get("fields_filled") or "-").strip()
	assurance_lower = assurance.lower()
	confirmation_msg = str(payload.get("confirmation_msg") or assurance or "").lower()
	submitted_yes = submitted_raw.strip().lower() == "yes"
	no_form_phrases = ["not found", "no form", "form not", "filled 0", "no contact", "unable to find"]
	has_no_form_signal = any(phrase in assurance_lower for phrase in no_form_phrases)

	# Primary: fields were filled and no "not found" signal
	form_found = (
		fields_filled not in ("-", "- none", "")
		and not has_no_form_signal
	)

	# Secondary: if submitted=Yes and the redirect URL contains contact-related
	# keywords (e.g. /contact-us, /contact), the form was found via URL-encoded
	# (GET) submission even if fields_filled wasn't tracked.
	if not form_found and submitted_yes and not has_no_form_signal:
		contact_url_frags = ["/contact", "contact-us", "contact_us", "contactus", "enquiry", "inquiry", "get-in-touch"]
		if any(frag in confirmation_msg for frag in contact_url_frags):
			form_found = True

	# Tertiary: if the scraper explicitly said submitted=Yes and there's no
	# explicit "not found" signal, trust the scraper's judgment.
	if not form_found and submitted_yes and not has_no_form_signal:
		form_found = True

	return {
		"companyName": str(payload.get("company_name") or "Unknown"),
		"contactUrl": str(payload.get("contact_url") or ""),
		"submitted": "Yes" if submitted_raw.strip().lower() == "yes" else "No",
		"status": _status_from_result(submitted_raw, captcha_status, submission_status, assurance),
		"captchaStatus": captcha_status,
		"confirmationMsg": str(payload.get("confirmation_msg") or assurance or "-") or "-",
		"estCostUsd": _parse_cost(payload.get("est_cost")),
		# ── Enriched fields for reporting ──
		"formFound": form_found,
		"fieldsFilled": fields_filled,
		"captchaPresent": captcha_present,
		"captchaType": str(payload.get("captcha_type") or "none"),
		"captchaResult": str(payload.get("captcha_result") or ""),
		"httpStatusCode": payload.get("http_status_code"),
		"errorDetail": str(payload.get("error_detail") or ""),
		"bandwidthKb": float(payload.get("bandwidth_kb") or 0),
		"domain": str(payload.get("domain") or ""),
	}


def _parse_result_line(line: str) -> dict[str, Any] | None:
	if not line.startswith(RESULT_PREFIX):
		return None

	try:
		raw_payload = json.loads(line[len(RESULT_PREFIX) :].strip())
		if not isinstance(raw_payload, dict):
			return None
		mapped = _map_result_payload(raw_payload)
		mapped["_raw_payload"] = raw_payload
		return mapped
	except Exception:
		return None


def _normalize_url_key(raw_url: str) -> str:
	normalized = str(raw_url or "").strip()
	if not normalized:
		return ""

	try:
		candidate = normalized if normalized.lower().startswith(("http://", "https://")) else f"https://{normalized}"
		parsed = urlparse(candidate)
		host = parsed.hostname.replace("www.", "", 1).lower() if parsed.hostname else ""
		path_name = parsed.path.rstrip("/") or "/"
		return f"{host}{path_name}" if host else normalized.lower()
	except Exception:
		return normalized.lower()


def _extract_lead_info(lead_data: dict[str, Any]) -> tuple[str, str]:
	if not isinstance(lead_data, dict):
		return "", ""
	normalized = {str(key or "").strip().lower(): _safe_trim(value) for key, value in lead_data.items() if value}
	raw_values = [value for value in normalized.values() if value]

	contact_url = ""
	# 1. Exact popular key matches
	for candidate in ("contact url found", "contact_url_found", "contact url", "contact_url", "contacturl", "url", "website", "site", "domain", "link"):
		if candidate in normalized:
			contact_url = normalized[candidate]
			break

	# 2. Fuzzy key matches
	if not contact_url:
		for key, value in normalized.items():
			if "url" in key or "link" in key or "website" in key or "domain" in key:
				contact_url = value
				break

	# 3. Value-based heuristics
	if not contact_url:
		for value in raw_values:
			lowered = value.lower()
			if lowered.startswith(("http://", "https://")) or ("." in lowered and " " not in value):
				contact_url = value
				break

	company_name = ""
	# 1. Exact popular key matches
	for candidate in ("company name", "company_name", "companyname", "company", "name", "business", "organization", "organisation"):
		if candidate in normalized:
			company_name = normalized[candidate]
			break

	# 2. Value-based heuristics (grab the first text that isn't the URL)
	if not company_name and raw_values:
		for value in raw_values:
			if value != contact_url:
				company_name = value
				break
		if not company_name:
			company_name = raw_values[0]

	return company_name, contact_url


def _read_leads_from_csv(csv_path: str) -> list[dict[str, Any]]:
	path_obj = Path(csv_path)
	if not path_obj.exists() or path_obj.is_dir():
		raise HTTPException(status_code=400, detail=f"CSV file not found: {csv_path}")

	leads: list[dict[str, Any]] = []
	try:
		with path_obj.open("r", encoding="utf-8-sig", newline="") as handle:
			reader = csv.DictReader(handle)
			if not reader.fieldnames:
				return leads

			for row in reader:
				company_name, contact_url = _extract_lead_info(row)
				if contact_url:
					leads.append({"companyName": company_name, "contactUrl": contact_url})
	except HTTPException:
		raise
	except Exception as exc:
		raise HTTPException(status_code=400, detail=f"Unable to parse CSV leads: {exc}") from exc

	return leads


def _prepare_dataset_from_leads(
	leads: list[dict[str, Any]],
	run_id: str,
	*,
	skip_url_keys: set[str] | None = None,
	skip_domains: set[str] | None = None,
	dedupe_by_domain: bool = True,
	max_urls_per_domain: int = 1,
	replied_url_keys: set[str] | None = None,
) -> tuple[str, int, int, int, int, int, int, int]:
	runs_dir = BASE_DIR / ".outreach-runs"
	runs_dir.mkdir(parents=True, exist_ok=True)
	dataset_path = runs_dir / f"run-{run_id}.json"

	seen: set[str] = set()
	domain_counts: dict[str, int] = {}
	duplicates_skipped = 0
	social_skipped = 0
	resume_skipped = 0
	invalid_skipped = 0
	already_submitted_skipped = 0
	replied_skipped = 0
	resume_keys = skip_url_keys or set()
	resume_domain_set = {str(item or "").strip().lower() for item in (skip_domains or set()) if str(item or "").strip()}
	max_domain_rows = max(1, int(max_urls_per_domain))
	rows: list[tuple[str, str]] = []

	# Load globally submitted URLs for cross-campaign deduplication
	global_submitted_urls: set[str] = set()
	if _db_available and _db_pool is not None:
		conn = _db_get_conn()
		try:
			with conn.cursor() as cur:
				cur.execute("SELECT contact_url FROM global_submitted_contacts")
				for (url,) in cur:
					global_submitted_urls.add(str(url or "").strip())
		except Exception:
			pass
		finally:
			_db_put_conn(conn)

	for index, lead in enumerate(leads):
		company_name, contact_url = _extract_lead_info(lead or {})
		if not company_name:
			company_name = f"Lead {index + 1}"

		if not contact_url:
			invalid_skipped += 1
			continue

		try:
			normalized_url, domain, url_key = _normalize_contact_url(contact_url)
		except HTTPException as exc:
			detail_text = _safe_trim(getattr(exc, "detail", ""))
			if detail_text == SOCIAL_URL_DETAIL:
				social_skipped += 1
			else:
				invalid_skipped += 1
			continue

		if url_key in seen:
			duplicates_skipped += 1
			continue

		seen.add(url_key)

		# Cross-campaign dedup: skip URLs already submitted globally
		if normalized_url in global_submitted_urls:
			already_submitted_skipped += 1
			continue

		# Reply-status skip: if the contact has a reply_status set (interested or not_interested),
		# skip it — the user has already handled this lead.
		if replied_url_keys and url_key in replied_url_keys:
			replied_skipped += 1
			continue

		if resume_keys and url_key in resume_keys:
			resume_skipped += 1
			continue
		if resume_domain_set and domain in resume_domain_set:
			resume_skipped += 1
			continue

		if dedupe_by_domain:
			domain_count = domain_counts.get(domain, 0)
			if domain_count >= max_domain_rows:
				duplicates_skipped += 1
				continue
			domain_counts[domain] = domain_count + 1

		rows.append((company_name, normalized_url))

	if not rows:
		if resume_skipped > 0:
			raise HTTPException(
				status_code=409,
				detail="Resume bookmark already covers all provided leads; nothing new to process.",
			)
		raise HTTPException(status_code=422, detail="No valid leads were provided")

	with dataset_path.open("w", encoding="utf-8") as handle:
		json.dump([{"company": row[0], "website": row[1]} for row in rows], handle, indent=2)

	return str(dataset_path), len(rows), duplicates_skipped, social_skipped, resume_skipped, invalid_skipped, already_submitted_skipped, replied_skipped


def _count_dataset_items(data_path: str | None) -> int:
	if not data_path:
		return 0

	try:
		path_obj = Path(data_path)
		with path_obj.open("r", encoding="utf-8") as handle:
			if str(data_path).endswith(".json"):
				data = json.load(handle)
				return len(data) if isinstance(data, list) else 0
			else:
				lines = [line for line in handle.read().splitlines() if line.strip()]
				return max(0, len(lines) - 1) if len(lines) > 1 else 0
	except Exception:
		return 0


def _build_persona_env(persona: dict[str, Any] | None) -> dict[str, str]:
	if not isinstance(persona, dict):
		return {}

	mapping: dict[str, str] = {
		"firstName": "MY_FIRST_NAME",
		"lastName": "MY_LAST_NAME",
		"professionalEmail": "MY_EMAIL",
		"verifiedPhone": "MY_PHONE",
		"company": "MY_COMPANY",
		"website": "MY_WEBSITE",
		"zipCode": "MY_PIN_CODE",
		"jobTitle": "MY_JOB_TITLE",
		"pitchMessage": "PITCH_MESSAGE",
		"id": "CAMPAIGN_ID",
		"title": "CAMPAIGN_TITLE",
		"aiInstruction": "AI_INSTRUCTION",
	}

	env: dict[str, str] = {}
	for key, env_key in mapping.items():
		value = persona.get(key)
		if value is None:
			continue
		text = str(value).strip()
		if text:
			env[env_key] = text

	max_daily = persona.get("maxDailySubmissions")
	if isinstance(max_daily, (int, float)) and int(max_daily) > 0:
		env["OUTREACH_MAX_DAILY_SUBMISSIONS"] = str(int(max_daily))
	
	if persona.get("breakFlag"):
		env["OUTREACH_BREAK_ON_FAILURE"] = "1"

	# Pass steps — use the first enabled step's aiInstruction as AI_INSTRUCTION
	steps = persona.get("steps")
	if isinstance(steps, list) and steps:
		enabled_steps = [s for s in steps if isinstance(s, dict) and s.get("enabled") is not False]
		if enabled_steps:
			first_instruction = str(enabled_steps[0].get("aiInstruction", "")).strip()
			if first_instruction:
				env["AI_INSTRUCTION"] = first_instruction
		try:
			env["CAMPAIGN_STEPS"] = json.dumps(steps)
		except Exception:
			pass

	full_name = f"{env.get('MY_FIRST_NAME', '')} {env.get('MY_LAST_NAME', '')}".strip()
	if full_name:
		env["MY_FULL_NAME"] = full_name


def _advance_contact_step(campaign_id: str, contact_url: str, steps: list) -> None:
	"""After successful form submission, advance the contact to the next campaign step.
	If the next step has a delay (type='normal'), schedule it for the future."""
	if not _db_available or not _db_pool or not campaign_id or not contact_url:
		return
	conn = _db_get_conn()
	try:
		with conn.cursor() as cur:
			cur.execute(
				"SELECT current_step_index FROM campaign_contacts WHERE campaign_id = %s AND url_key = %s",
				(campaign_id, contact_url)
			)
			row = cur.fetchone()
			if not row:
				return
			current = row[0] or 0
			next_idx = current + 1

			# Filter to only enabled steps
			enabled_steps = [s for s in steps if isinstance(s, dict) and s.get("enabled") is not False]

			if next_idx >= len(enabled_steps):
				# All steps complete for this contact
				cur.execute(
					"UPDATE campaign_contacts SET current_step_index = %s "
					"WHERE campaign_id = %s AND url_key = %s",
					(next_idx, campaign_id, contact_url)
				)
			else:
				next_step = enabled_steps[next_idx]
				delay_val = int(next_step.get("delayValue", 0) or 0)
				delay_unit = str(next_step.get("delayUnit", "days") or "days").strip().lower()

				if next_step.get("type") == "immediate" or delay_val <= 0:
					cur.execute(
						"UPDATE campaign_contacts SET current_step_index = %s, next_attempt_at = NOW() "
						"WHERE campaign_id = %s AND url_key = %s",
						(next_idx, campaign_id, contact_url)
					)
				else:
					# Schedule for later
					if delay_unit == "hours":
						interval_sql = f"{delay_val} hours"
					else:
						interval_sql = f"{delay_val} days"
					cur.execute(
						f"UPDATE campaign_contacts SET current_step_index = %s, "
						f"next_attempt_at = NOW() + INTERVAL '{interval_sql}' "
						f"WHERE campaign_id = %s AND url_key = %s",
						(next_idx, campaign_id, contact_url)
					)
			conn.commit()
			print(f"[Steps] Advanced {contact_url} to step {next_idx} in campaign {campaign_id}")
	except Exception as exc:
		print(f"[Steps] Warning: failed to advance step for {contact_url}: {exc}")
		try:
			conn.rollback()
		except Exception:
			pass
	finally:
		_db_put_conn(conn)




def _append_log(line: str, run_id: str) -> None:
	clean = line.rstrip("\r\n")
	if not clean:
		return
	print(clean, flush=True)
	processed_leads = None
	campaign_id = None
	user_id = None
	parsed_result = _parse_result_line(clean)
	with _state_lock:
		state = _active_runs.get(run_id)
		if state is None:
			return
		state["logs"].append(clean)
		campaign_id = state["campaign_id"]
		user_id = state["user_id"]
		if parsed_result is not None:
			if campaign_id and not parsed_result.get("campaignId"):
				parsed_result["campaignId"] = campaign_id
			campaign_title = state["campaign_title"]
			if campaign_title and not parsed_result.get("campaignTitle"):
				parsed_result["campaignTitle"] = campaign_title
			state["results"].append(parsed_result)
			state["processed_leads"] = len(state["results"])
			processed_leads = state["processed_leads"]
			current_lead = str(parsed_result.get("contactUrl") or "").strip() or str(parsed_result.get("companyName") or "-")
			state["current_lead"] = current_lead
	_db_append_log(run_id, clean)
	if parsed_result is not None:
		_db_record_result(run_id, campaign_id, user_id, parsed_result)
		# Advance contact to next campaign step on successful submission
		is_success = str(parsed_result.get("submitted") or "").strip().lower() == "yes"
		if is_success and campaign_id:
			contact_url = _normalize_url_key(str(parsed_result.get("contactUrl") or ""))
			# Retrieve steps from the persona env that was passed at run start
			steps_json = None
			with _state_lock:
				st = _active_runs.get(run_id)
				if st:
					steps_json = st.get("campaign_steps")
			if steps_json and contact_url:
				try:
					steps_list = json.loads(steps_json) if isinstance(steps_json, str) else steps_json
					_advance_contact_step(campaign_id, contact_url, steps_list)
				except Exception as exc:
					print(f"[Steps] Warning: step advance parse error: {exc}")
	if processed_leads is not None:
		with _state_lock:
			state = _active_runs.get(run_id)
			if state:
				_db_update_run_state(
					run_id,
					status="running",
					processed_leads=processed_leads,
					total_leads=state["total_leads"],
					duplicates_skipped=state["duplicates_skipped"],
					resume_skipped_leads=state["resume_skipped_leads"],
					social_skipped_leads=state["social_skipped_leads"],
				)


def _stream_process_output(proc: subprocess.Popen, run_id: str) -> None:
	if proc.stdout is None:
		return
	for line in proc.stdout:
		_append_log(line, run_id)
	proc.stdout.close()


def _refresh_process_state() -> None:
	completed_runs: list[tuple[str, dict]] = []
	with _state_lock:
		for run_id, state in _active_runs.items():
			proc = state.get("process")
			if proc is None:
				continue
			code = proc.poll()
			if code is None:
				continue
			reader_thread = state.get("reader_thread")
			if reader_thread and reader_thread.is_alive():
				continue
			if state.get("exit_code") is None:
				state["exit_code"] = int(code)
				state["finished_at"] = _utc_now_iso()
				completed_runs.append((run_id, state))

	for run_id, state in completed_runs:
		exit_code = state["exit_code"]
		status = "completed" if int(exit_code) == 0 else "failed"
		_db_update_run_state(
			run_id,
			status=status,
			finished_at=state["finished_at"],
			exit_code=exit_code,
			processed_leads=state["processed_leads"],
			total_leads=state["total_leads"],
			duplicates_skipped=state["duplicates_skipped"],
			resume_skipped_leads=state["resume_skipped_leads"],
			social_skipped_leads=state["social_skipped_leads"],
		)
		csv_file = state.get("generated_csv_path")
		if csv_file:
			try:
				Path(csv_file).unlink(missing_ok=True)
			except Exception:
				pass

	# Clean up finished runs from _active_runs to prevent stale 409 errors
	if completed_runs:
		with _state_lock:
			for run_id, _ in completed_runs:
				_active_runs.pop(run_id, None)


def _resolve_csv_path(csv_path: str | None) -> str | None:
	if not csv_path:
		return None

	candidate = Path(csv_path).expanduser()
	if not candidate.is_absolute():
		candidate = (BASE_DIR / candidate).resolve()

	if not candidate.exists():
		raise HTTPException(status_code=400, detail=f"CSV file not found: {candidate}")
	if candidate.is_dir():
		raise HTTPException(status_code=400, detail=f"CSV path is a directory: {candidate}")

	return str(candidate)


def _validate_ping_url(url: str) -> str:
	parsed = urlparse(url)
	if parsed.scheme not in {"http", "https"} or not parsed.netloc:
		raise HTTPException(status_code=400, detail="Use a valid http/https URL")
	return url


@app.get("/")
def root() -> dict:
	return {
		"service": "Outreach FastAPI Backend",
		"docs": "/docs",
		"start_endpoint": "/outreach/start",
		"start_endpoint_aliases": ["/api/outreach/start", "/api/start-run"],
	}


@app.get("/health")
def health() -> dict:
	_refresh_process_state()
	return {
		"status": "ok",
		"db_connected": _db_available,
		"db_engine": "postgresql",
	}


@app.get("/db/status")
def db_status() -> dict:
	return {
		"db_connected": _db_available,
		"db_engine": "postgresql",
		"database_url": DATABASE_URL.split("@")[-1], # Mask credentials
		"db_init_error": _db_init_error,
	}


@app.get("/ping")
def ping() -> dict:
	_refresh_process_state()
	with _state_lock:
		running_count = _count_running()
		return {
			"status": "ok",
			"checked_at": _utc_now_iso(),
			"outreach_running": running_count > 0,
			"active_runs": running_count,
			"db_connected": _db_available,
		}


# --- API Core Helpers ---

def _get_user_context(request: Request) -> tuple[str, bool]:
	"""Extracts user_id and is_admin from headers."""
	user_id = _safe_trim(request.headers.get("X-User-Id", ""))
	is_admin = request.headers.get("X-Is-Admin", "").lower() == "true"
	return user_id, is_admin

def _ensure_record_ownership(table: str, id_col: str, record_id: str, user_id: str, is_admin: bool):
	"""Raises 404 if record doesn't exist or doesn't belong to the user (and user is not admin)."""
	if not _db_available or _db_pool is None:
		raise HTTPException(status_code=503, detail="Database is not connected")
	
	if is_admin:
		# Admins only need to know if it exists
		where_clause = f"{id_col} = %s"
		params = [record_id]
	else:
		if not user_id:
			raise HTTPException(status_code=401, detail="User identification required")
		where_clause = f"{id_col} = %s AND user_id = %s"
		params = [record_id, user_id]
	
	conn = _db_get_conn()
	try:
		with conn.cursor() as cur:
			cur.execute(f"SELECT COUNT(*) FROM {table} WHERE {where_clause}", params)
			if cur.fetchone()[0] == 0:
				# If we are in user-mode and it doesn't exist, we return 404 to avoid leaking existence
				raise HTTPException(status_code=404, detail=f"Record '{record_id}' not found")
	finally:
		_db_put_conn(conn)

# --- API Endpoints ---

@app.get("/endpoint/ping")
def ping_endpoint(
	url: str = Query(..., description="Full http/https URL to ping"),
	timeout: float = Query(default=8.0, ge=1.0, le=30.0),
) -> dict:
	target = _validate_ping_url(url)
	request = urlrequest.Request(target, method="GET", headers={"User-Agent": "OutreachFastAPI/1.0"})
	start = time.perf_counter()

	try:
		with urlrequest.urlopen(request, timeout=timeout) as response:
			status_code = int(response.status)
			reason = str(getattr(response, "reason", ""))
			ok = 200 <= status_code < 400
	except urlerror.HTTPError as exc:
		status_code = int(exc.code)
		reason = str(exc.reason)
		ok = False
	except Exception as exc:
		elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
		return {
			"target": target,
			"ok": False,
			"status_code": None,
			"reason": str(exc),
			"response_time_ms": elapsed_ms,
			"checked_at": _utc_now_iso(),
		}

	elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
	return {
		"target": target,
		"ok": ok,
		"status_code": status_code,
		"reason": reason,
		"response_time_ms": elapsed_ms,
		"checked_at": _utc_now_iso(),
	}


@app.get("/campaigns")
@app.get("/api/campaigns")
def list_campaigns(
	request: Request,
	q: str | None = Query(default=None),
	page: int = Query(default=1, ge=1),
	limit: int = Query(default=25, ge=1, le=200),
) -> dict:
	if not _db_available or _db_pool is None:
		raise HTTPException(status_code=503, detail="Database is not connected")
		
	user_id, is_admin = _get_user_context(request)
	offset = (int(page) - 1) * int(limit)
	where_sql, search_params = _build_search_filter_sql(q, ["campaign_id", "name", "status", "ai_instruction"])
	
	# Scoping
	if not is_admin:
		if not user_id:
			return { "campaigns": [], "pagination": _build_pagination_meta(page, limit, 0), "query": {"q": _safe_trim(q)} }
		where_sql = f"({where_sql}) AND user_id = %s"
		search_params.append(user_id)
	
	conn = _db_get_conn()
	try:
		with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
			# Count total
			count_sql = f"SELECT COUNT(*) FROM campaigns WHERE {where_sql}"
			cur.execute(count_sql, search_params)
			total = cur.fetchone()[0]
			
			# Fetch campaigns
			fetch_sql = f"SELECT * FROM campaigns WHERE {where_sql} ORDER BY updated_at DESC OFFSET %s LIMIT %s"
			params = search_params + [offset, limit]
			cur.execute(fetch_sql, params)
			campaign_docs = [dict(row) for row in cur]
			
			if not campaign_docs:
				return {
					"campaigns": [],
					"pagination": _build_pagination_meta(page, limit, total),
					"query": {"q": _safe_trim(q)},
				}
				
			campaign_ids = [doc["campaign_id"] for doc in campaign_docs]
			
			# Fetch contact counts
			cur.execute("""
				SELECT campaign_id, COUNT(*) as count
				FROM campaign_contacts
				WHERE campaign_id IN %s
				GROUP BY campaign_id
			""", (tuple(campaign_ids),))
			contact_counts = {row["campaign_id"]: row["count"] for row in cur}
			
		items = []
		for doc in campaign_docs:
			cid = doc["campaign_id"]
			items.append(_map_campaign_document(
				doc,
				contact_count=contact_counts.get(cid, 0),
				last_run=_campaign_last_run(cid)
			))
			
		return {
			"campaigns": items,
			"pagination": _build_pagination_meta(page, limit, total),
			"query": {"q": _safe_trim(q)},
		}
	except Exception as exc:
		raise HTTPException(status_code=500, detail=f"Unable to list campaigns: {exc}")
	finally:
		_db_put_conn(conn)


@app.post("/campaigns")
@app.post("/api/campaigns")
def create_campaign(request: Request, payload: CampaignCreateRequest) -> dict:
	if not _db_available or _db_pool is None:
		raise HTTPException(status_code=503, detail="Database is not connected")
		
	user_id = _safe_trim(request.headers.get("X-User-Id", ""))
	now = _utc_now_iso()
	campaign_id = f"cmp-{uuid.uuid4().hex[:10]}"
	
	doc = {
		"campaign_id": campaign_id,
		"name": _safe_trim(payload.name),
		"status": _normalize_campaign_status(payload.status),
		"ai_instruction": _safe_trim(payload.aiInstruction),
		"max_daily_submissions": int(payload.maxDailySubmissions),
		"search_for_form": bool(payload.searchForForm),
		"break_flag": bool(payload.breakFlag),
		"steps": payload.steps or [],
		"created_at": now,
		"updated_at": now,
		"user_id": user_id or None,
	}
	
	conn = _db_get_conn()
	try:
		with conn.cursor() as cur:
			cur.execute("""
				INSERT INTO campaigns (
					campaign_id, name, status, ai_instruction, max_daily_submissions,
					search_for_form, break_flag, steps, created_at, updated_at, user_id
				) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
			""", (
				doc["campaign_id"], doc["name"], doc["status"], doc["ai_instruction"],
				doc["max_daily_submissions"], doc["search_for_form"], doc["break_flag"],
				psycopg2.extras.Json(doc["steps"]), doc["created_at"], doc["updated_at"],
				doc["user_id"]
			))
			conn.commit()
		return _map_campaign_document(doc, contact_count=0, last_run=None)
	except psycopg2.IntegrityError:
		raise HTTPException(status_code=409, detail="Campaign ID collision, please retry")
	except Exception as exc:
		raise HTTPException(status_code=500, detail=f"Unable to create campaign: {exc}")
	finally:
		_db_put_conn(conn)


@app.get("/campaigns/{campaign_id}")
@app.get("/api/campaigns/{campaign_id}")
def get_campaign(request: Request, campaign_id: str) -> dict:
	user_id, is_admin = _get_user_context(request)
	doc = _ensure_campaign_exists(campaign_id, user_id, is_admin)
	
	conn = _db_get_conn()
	try:
		with conn.cursor() as cur:
			cur.execute("SELECT COUNT(*) FROM campaign_contacts WHERE campaign_id = %s", (campaign_id,))
			count = cur.fetchone()[0]
		return _map_campaign_document(doc, contact_count=count, last_run=_campaign_last_run(campaign_id))
	except Exception as exc:
		raise HTTPException(status_code=500, detail=f"Unable to fetch campaign: {exc}")
	finally:
		_db_put_conn(conn)


@app.put("/campaigns/{campaign_id}")
@app.put("/api/campaigns/{campaign_id}")
def update_campaign(request: Request, campaign_id: str, payload: CampaignUpdateRequest) -> dict:
	user_id, is_admin = _get_user_context(request)
	_ensure_campaign_exists(campaign_id, user_id, is_admin)

	updates: list[str] = []
	params: list[Any] = []
	raw = payload.model_dump(exclude_unset=True)
	
	for key, value in raw.items():
		if key == "name":
			updates.append("name = %s")
			params.append(_safe_trim(value))
		elif key == "aiInstruction":
			updates.append("ai_instruction = %s")
			params.append(_safe_trim(value))
		elif key == "status":
			updates.append("status = %s")
			params.append(_normalize_campaign_status(str(value)))
		elif key == "maxDailySubmissions" and value is not None:
			updates.append("max_daily_submissions = %s")
			params.append(int(value))
		elif key == "searchForForm" and value is not None:
			updates.append("search_for_form = %s")
			params.append(bool(value))
		elif key == "breakFlag" and value is not None:
			updates.append("break_flag = %s")
			params.append(bool(value))
		elif key == "steps" and value is not None:
			updates.append("steps = %s")
			params.append(psycopg2.extras.Json(value))
		elif key == "scheduleDay":
			updates.append("schedule_day = %s")
			params.append(_safe_trim(value))
		elif key == "scheduleTime":
			updates.append("schedule_time = %s")
			params.append(_safe_trim(value))

	if updates:
		updates.append("updated_at = %s")
		params.append(_utc_now_iso())
		params.append(campaign_id)
		sql = f"UPDATE campaigns SET {', '.join(updates)} WHERE campaign_id = %s"
		
		conn = _db_get_conn()
		try:
			with conn.cursor() as cur:
				cur.execute(sql, tuple(params))
				conn.commit()
		except Exception as exc:
			raise HTTPException(status_code=500, detail=f"Unable to update campaign: {exc}")
		finally:
			_db_put_conn(conn)

	return get_campaign(request, campaign_id)


@app.delete("/campaigns/{campaign_id}")
@app.delete("/api/campaigns/{campaign_id}")
def delete_campaign(request: Request, campaign_id: str) -> dict:
	user_id, is_admin = _get_user_context(request)
	_ensure_campaign_exists(campaign_id, user_id, is_admin)

	conn = _db_get_conn()
	try:
		with conn.cursor() as cur:
			# Clear foreign key references in global_submission_lock
			cur.execute("UPDATE global_submission_lock SET first_campaign_id = NULL WHERE first_campaign_id = %s", (campaign_id,))
			
			# Delete contacts first due to foreign key constraints if applied (though not explicitly set yet)
			cur.execute("DELETE FROM campaign_contacts WHERE campaign_id = %s", (campaign_id,))
			deleted_contacts = cur.rowcount
			
			# Also clean up outreach_runs and outreach_results just in case
			cur.execute("DELETE FROM outreach_runs WHERE campaign_id = %s", (campaign_id,))
			cur.execute("DELETE FROM outreach_results WHERE campaign_id = %s", (campaign_id,))
			
			cur.execute("DELETE FROM campaigns WHERE campaign_id = %s", (campaign_id,))
			conn.commit()
			
		return {
			"status": "deleted",
			"campaign_id": campaign_id,
			"deleted_contacts": int(deleted_contacts),
		}
	except Exception as exc:
		raise HTTPException(status_code=500, detail=f"Unable to delete campaign: {exc}")
	finally:
		_db_put_conn(conn)


@app.get("/campaigns/{campaign_id}/contacts")
@app.get("/api/campaigns/{campaign_id}/contacts")
def list_campaign_contacts(
	request: Request,
	campaign_id: str,
	q: str | None = Query(default=None),
	page: int = Query(default=1, ge=1),
	limit: int = Query(default=5000, ge=1, le=5000),
) -> dict:
	user_id, is_admin = _get_user_context(request)
	_ensure_campaign_exists(campaign_id, user_id, is_admin)
	if not _db_available or _db_pool is None:
		raise HTTPException(status_code=503, detail="Database is not connected")
		
	offset = (int(page) - 1) * int(limit)
	where_sql, search_params = _build_search_filter_sql(q, ["company_name", "contact_url", "domain", "location", "industry", "notes"])
	
	conn = _db_get_conn()
	try:
		with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
			cur.execute(f"SELECT COUNT(*) FROM campaign_contacts WHERE campaign_id = %s AND {where_sql}", [campaign_id] + search_params)
			total = cur.fetchone()[0]
			
			cur.execute(f"""
				SELECT * FROM campaign_contacts
				WHERE campaign_id = %s AND {where_sql}
				ORDER BY updated_at DESC OFFSET %s LIMIT %s
			""", [campaign_id] + search_params + [offset, limit])
			docs = [dict(row) for row in cur]
			
		return {
			"contacts": [_map_contact_document(doc) for doc in docs],
			"pagination": _build_pagination_meta(page, limit, total),
			"query": {"q": _safe_trim(q), "campaign_id": campaign_id},
		}
	except Exception as exc:
		raise HTTPException(status_code=500, detail=f"Unable to list contacts: {exc}")
	finally:
		_db_put_conn(conn)


@app.post("/campaigns/{campaign_id}/contacts")
@app.post("/api/campaigns/{campaign_id}/contacts")
def create_campaign_contact(request: Request, campaign_id: str, payload: CampaignContactCreateRequest) -> dict:
	user_id, is_admin = _get_user_context(request)
	_ensure_campaign_exists(campaign_id, user_id, is_admin)
	if not _db_available or _db_pool is None:
		raise HTTPException(status_code=503, detail="Database is not connected")

	normalized_url, domain, url_key = _normalize_contact_url(payload.contactUrl)
	now = _utc_now_iso()
	doc = {
		"contact_id": f"lead-{uuid.uuid4().hex[:10]}",
		"campaign_id": campaign_id,
		"company_name": _safe_trim(payload.companyName),
		"contact_url": normalized_url,
		"domain": domain,
		"url_key": url_key,
		"location": _safe_trim(payload.location),
		"industry": _safe_trim(payload.industry),
		"notes": _safe_trim(payload.notes),
		"created_at": now,
		"updated_at": now,
		"user_id": user_id or None,
	}

	conn = _db_get_conn()
	try:
		with conn.cursor() as cur:
			cur.execute("""
				INSERT INTO campaign_contacts (
					contact_id, campaign_id, company_name, contact_url, domain, url_key,
					location, industry, notes, created_at, updated_at, user_id
				) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
			""", (
				doc["contact_id"], doc["campaign_id"], doc["company_name"], doc["contact_url"],
				doc["domain"], doc["url_key"], doc["location"], doc["industry"], doc["notes"],
				doc["created_at"], doc["updated_at"], doc["user_id"]
			))
			conn.commit()
		return _map_contact_document(doc)
	except psycopg2.IntegrityError:
		raise HTTPException(status_code=409, detail="Contact URL already exists in this campaign")
	except Exception as exc:
		raise HTTPException(status_code=500, detail=f"Unable to create contact: {exc}")
	finally:
		_db_put_conn(conn)


@app.post("/campaigns/{campaign_id}/contacts/bulk")
@app.post("/api/campaigns/{campaign_id}/contacts/bulk")
def create_bulk_campaign_contacts(request: Request, campaign_id: str, payload: BulkContactsCreateRequest = Body(...)) -> dict:
	user_id, is_admin = _get_user_context(request)
	_ensure_campaign_exists(campaign_id, user_id, is_admin)
	if not _db_available or _db_pool is None:
		raise HTTPException(status_code=503, detail="Database is not connected")
	
	now = _utc_now_iso()
	docs_to_insert = []
	seen_url_keys = set()
	skipped_no_url = 0
	skipped_invalid = 0
	skipped_dup = 0
	
	for item in payload.contacts:
		company_name, contact_url = _extract_lead_info(item)
		if not contact_url:
			skipped_no_url += 1
			continue
		result = _normalize_contact_url_lenient(contact_url)
		if result is None:
			skipped_invalid += 1
			continue
		normalized_url, domain, url_key = result
			
		if url_key in seen_url_keys:
			skipped_dup += 1
			continue
		seen_url_keys.add(url_key)
			
		docs_to_insert.append((
			f"lead-{uuid.uuid4().hex[:10]}",
			campaign_id,
			_safe_trim(company_name) or "Unknown",
			normalized_url,
			domain,
			url_key,
			_safe_trim(item.get("location")),
			_safe_trim(item.get("industry")),
			_safe_trim(item.get("notes")),
			now,
			now,
			(int(user_id) if user_id and str(user_id).isdigit() else None)
		))
	
	print(f"[Bulk] Campaign {campaign_id}: received={len(payload.contacts)} valid={len(docs_to_insert)} no_url={skipped_no_url} invalid={skipped_invalid} dup={skipped_dup}")
	
	if not docs_to_insert:
		return {"message": "No valid contacts to process.", "skipped_no_url": skipped_no_url, "skipped_invalid": skipped_invalid, "skipped_dup": skipped_dup}

	conn = _db_get_conn()
	try:
		if not payload.force and seen_url_keys:
			# Check for duplicates across campaigns
			with conn.cursor() as cur:
				placeholders = ",".join(["%s"] * len(seen_url_keys))
				cur.execute(f"SELECT url_key FROM campaign_contacts WHERE url_key IN ({placeholders}) AND campaign_id != %s", (*seen_url_keys, campaign_id))
				existing_keys = {row[0] for row in cur.fetchall()}
				
				if existing_keys:
					# We have duplicates! Let's get the detailed info for the 409 response
					placeholders2 = ",".join(["%s"] * len(existing_keys))
					cur.execute(f"SELECT c.url_key, c.company_name, c.campaign_id, cmp.name FROM campaign_contacts c JOIN campaigns cmp ON c.campaign_id = cmp.campaign_id WHERE c.url_key IN ({placeholders2}) AND c.campaign_id != %s", (*existing_keys, campaign_id))
					
					duplicate_details = []
					for row in cur.fetchall():
						duplicate_details.append({
							"urlKey": row[0],
							"companyName": row[1],
							"campaignId": row[2],
							"campaignName": row[3]
						})
					
					raise HTTPException(
						status_code=409,
						detail={
							"message": "Duplicates detected across campaigns",
							"duplicates": duplicate_details
						}
					)

		inserted = 0
		with conn.cursor() as cur:
			# Use execute_values for efficient bulk insertion, batch 1000 at a time
			psycopg2.extras.execute_values(cur, """
				INSERT INTO campaign_contacts (
					contact_id, campaign_id, company_name, contact_url, domain, url_key,
					location, industry, notes, created_at, updated_at, user_id
				) VALUES %s
				ON CONFLICT (campaign_id, url_key) DO NOTHING
			""", docs_to_insert, page_size=1000)
			inserted = cur.rowcount
			conn.commit()
		print(f"[Bulk] Campaign {campaign_id}: inserted={inserted} db_dup_skipped={len(docs_to_insert) - inserted}")
		return {"message": f"Successfully processed {len(docs_to_insert)} contacts. Inserted {inserted}.", "inserted": inserted, "skipped_no_url": skipped_no_url, "skipped_invalid": skipped_invalid, "skipped_dup": skipped_dup}
	except HTTPException:
		raise
	except Exception as exc:
		import traceback
		import sys
		traceback.print_exc(file=sys.stdout)
		sys.stdout.flush()
		raise HTTPException(status_code=500, detail=f"Unable to process bulk contacts: {exc}")
	finally:
		_db_put_conn(conn)



@app.delete("/campaigns/{campaign_id}/contacts")
@app.delete("/api/campaigns/{campaign_id}/contacts")
def delete_all_campaign_contacts(request: Request, campaign_id: str) -> dict:
	user_id, is_admin = _get_user_context(request)
	_ensure_campaign_exists(campaign_id, user_id, is_admin)
	
	conn = _db_get_conn()
	try:
		with conn.cursor() as cur:
			cur.execute("DELETE FROM campaign_contacts WHERE campaign_id = %s", (campaign_id,))
			deleted_count = cur.rowcount
			conn.commit()
		return {
			"status": "deleted",
			"deleted_count": deleted_count,
			"campaign_id": campaign_id,
		}
	except Exception as exc:
		raise HTTPException(status_code=500, detail=f"Unable to delete contacts: {exc}")
	finally:
		_db_put_conn(conn)


@app.delete("/campaigns/{campaign_id}/contacts/{contact_id}")
@app.delete("/api/campaigns/{campaign_id}/contacts/{contact_id}")
def delete_campaign_contact(request: Request, campaign_id: str, contact_id: str) -> dict:
	user_id, is_admin = _get_user_context(request)
	_ensure_campaign_exists(campaign_id, user_id, is_admin)

	conn = _db_get_conn()
	try:
		with conn.cursor() as cur:
			cur.execute("DELETE FROM campaign_contacts WHERE campaign_id = %s AND contact_id = %s", (campaign_id, contact_id))
			if cur.rowcount == 0:
				raise HTTPException(status_code=404, detail="Contact not found")
			conn.commit()
		return {
			"status": "deleted",
			"campaign_id": campaign_id,
			"contact_id": contact_id,
		}
	except HTTPException:
		raise
	except Exception as exc:
		raise HTTPException(status_code=500, detail=f"Unable to delete contact: {exc}")
	finally:
		_db_put_conn(conn)


@app.patch("/campaigns/{campaign_id}/contacts/{contact_id}")
@app.patch("/api/campaigns/{campaign_id}/contacts/{contact_id}")
def update_campaign_contact(request: Request, campaign_id: str, contact_id: str, payload: ContactUpdateRequest) -> dict:
	user_id, is_admin = _get_user_context(request)
	_ensure_campaign_exists(campaign_id, user_id, is_admin)

	updates: list[str] = []
	params: list[Any] = []
	if payload.companyName is not None:
		updates.append("company_name = %s")
		params.append(_safe_trim(payload.companyName))
	if payload.isInterested is not None:
		updates.append("is_interested = %s")
		params.append(bool(payload.isInterested))
	if payload.replyStatus is not None:
		allowed_reply = ("", "interested", "not_interested")
		reply_val = _safe_trim(payload.replyStatus) or ""
		if reply_val not in allowed_reply:
			raise HTTPException(status_code=400, detail=f"replyStatus must be one of {allowed_reply}")
		updates.append("reply_status = %s")
		params.append(reply_val)

	if not updates:
		return {"status": "no changes"}

	updates.append("updated_at = %s")
	params.append(_utc_now_iso())
	params.append(campaign_id)
	params.append(contact_id)

	conn = _db_get_conn()
	try:
		with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
			sql = f"UPDATE campaign_contacts SET {', '.join(updates)} WHERE campaign_id = %s AND contact_id = %s RETURNING *"
			cur.execute(sql, tuple(params))
			row = cur.fetchone()
			if not row:
				raise HTTPException(status_code=404, detail="Contact not found")
			conn.commit()
			return _map_contact_document(dict(row))
	except HTTPException:
		raise
	except Exception as exc:
		raise HTTPException(status_code=500, detail=f"Unable to update contact: {exc}")
	finally:
		_db_put_conn(conn)


@app.get("/contacts")
@app.get("/api/contacts")
def list_all_contacts(
	request: Request,
	campaign_id: str | None = Query(default=None),
	q: str | None = Query(default=None),
	page: int = Query(default=1, ge=1),
	limit: int = Query(default=50, ge=1, le=200000),
) -> dict:
	if not _db_available or _db_pool is None:
		raise HTTPException(status_code=503, detail="Database is not connected")
		
	user_id, is_admin = _get_user_context(request)
	offset = (int(page) - 1) * int(limit)
	where_clauses: list[str] = ["1=1"]
	params: list[Any] = []

	if campaign_id:
		where_clauses.append("campaign_id = %s")
		params.append(campaign_id)
	
	# Filter contacts to only those belonging to user's campaigns
	if not is_admin:
		if not user_id:
			return { "contacts": [], "pagination": _build_pagination_meta(page, limit, 0), "query": {"q": _safe_trim(q)} }
		where_clauses.append("user_id = %s")
		params.append(user_id)
		
	search_sql, search_params = _build_search_filter_sql(q, ["company_name", "contact_url", "domain", "location", "industry", "notes"])
	if search_sql != "1=1":
		where_clauses.append(search_sql)
		params.extend(search_params)

	conn = _db_get_conn()
	try:
		with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
			# Get campaign name map (scoped to user if available)
			if is_admin:
				cur.execute("SELECT campaign_id, name FROM campaigns")
			else:
				cur.execute("SELECT campaign_id, name FROM campaigns WHERE user_id = %s", (user_id,))
			campaign_name_map = {row["campaign_id"]: row["name"] for row in cur}
			
			# Count total
			where_str = " AND ".join(where_clauses)
			cur.execute(f"SELECT COUNT(*) FROM campaign_contacts WHERE {where_str}", params)
			total = cur.fetchone()[0]
			
			# Fetch contacts
			cur.execute(f"""
				SELECT * FROM campaign_contacts
				WHERE {where_str}
				ORDER BY updated_at DESC OFFSET %s LIMIT %s
			""", params + [offset, limit])
			contact_docs = [dict(row) for row in cur]
			
		items = []
		for doc in contact_docs:
			mapped = _map_contact_document(doc)
			mapped["campaignName"] = campaign_name_map.get(mapped["campaignId"], "")
			items.append(mapped)

		return {
			"contacts": items,
			"pagination": _build_pagination_meta(page, limit, total),
			"query": {"q": _safe_trim(q), "campaign_id": _safe_trim(campaign_id)},
		}
	except Exception as exc:
		raise HTTPException(status_code=500, detail=f"Unable to list contacts: {exc}")
	finally:
		_db_put_conn(conn)


@app.delete("/api/contacts/{contact_id}")
def delete_contact_global(request: Request, contact_id: str) -> dict:
	user_id, is_admin = _get_user_context(request)
	contact_id_clean = _safe_trim(contact_id)
	if not contact_id_clean:
		raise HTTPException(status_code=400, detail="Invalid contact ID")
		
	conn = _db_get_conn()
	try:
		with conn.cursor() as cur:
			if is_admin:
				cur.execute("DELETE FROM campaign_contacts WHERE contact_id = %s", (contact_id_clean,))
			else:
				if not user_id:
					raise HTTPException(status_code=401, detail="User identification required")
				cur.execute("DELETE FROM campaign_contacts WHERE contact_id = %s AND user_id = %s", (contact_id_clean, user_id))
			if cur.rowcount == 0:
				raise HTTPException(status_code=404, detail="Contact not found")
			conn.commit()
		return {"message": "Contact deleted successfully"}
	except HTTPException:
		raise
	except Exception as exc:
		raise HTTPException(status_code=500, detail=f"Unable to delete contact: {exc}")
	finally:
		_db_put_conn(conn)


@app.delete("/api/contacts")
def delete_all_contacts(request: Request) -> dict:
	user_id, is_admin = _get_user_context(request)
	conn = _db_get_conn()
	try:
		with conn.cursor() as cur:
			if is_admin:
				cur.execute("DELETE FROM campaign_contacts")
			else:
				if not user_id:
					raise HTTPException(status_code=401, detail="User identification required")
				cur.execute("DELETE FROM campaign_contacts WHERE user_id = %s", (user_id,))
			count = cur.rowcount
			conn.commit()
		return {"message": f"Successfully deleted {count} contacts"}
	except Exception as exc:
		raise HTTPException(status_code=500, detail=f"Unable to delete contacts: {exc}")
	finally:
		_db_put_conn(conn)


@app.post("/api/contacts/bulk")
def create_bulk_contacts(request: Request, payload: BulkContactsCreateRequest = Body(...)) -> dict:
	if not _db_available or _db_pool is None:
		raise HTTPException(status_code=503, detail="Database is not connected")
	
	user_id, is_admin = _get_user_context(request)
	
	now = _utc_now_iso()
	docs_to_insert = []
	seen_url_keys = set()
	skipped_no_url = 0
	skipped_invalid = 0
	skipped_dup = 0
	
	for item in payload.contacts:
		company_name, contact_url = _extract_lead_info(item)
		if not contact_url:
			skipped_no_url += 1
			continue
		result = _normalize_contact_url_lenient(contact_url)
		if result is None:
			skipped_invalid += 1
			continue
		normalized_url, domain, url_key = result
		
		if url_key in seen_url_keys:
			skipped_dup += 1
			continue
		seen_url_keys.add(url_key)
			
		docs_to_insert.append((
			f"lead-{uuid.uuid4().hex[:10]}",
			"", # No campaign ID
			_safe_trim(company_name) or "Unknown",
			normalized_url,
			domain,
			url_key,
			"", "", "", # location, industry, notes
			now,
			now,
			(int(user_id) if user_id and str(user_id).isdigit() else None)
		))
	
	print(f"[Bulk] Global: received={len(payload.contacts)} valid={len(docs_to_insert)} no_url={skipped_no_url} invalid={skipped_invalid} dup={skipped_dup}")
	
	if not docs_to_insert:
		return {"message": "No valid contacts to process.", "skipped_no_url": skipped_no_url, "skipped_invalid": skipped_invalid, "skipped_dup": skipped_dup}

	conn = _db_get_conn()
	try:
		with conn.cursor() as cur:
			psycopg2.extras.execute_values(cur, """
				INSERT INTO campaign_contacts (
					contact_id, campaign_id, company_name, contact_url, domain, url_key,
					location, industry, notes, created_at, updated_at, user_id
				) VALUES %s
				ON CONFLICT (campaign_id, url_key) DO NOTHING
			""", docs_to_insert, page_size=1000)
			inserted = cur.rowcount
			conn.commit()
		print(f"[Bulk] Global: inserted={inserted} db_dup_skipped={len(docs_to_insert) - inserted}")
		return {"message": f"Successfully processed {len(docs_to_insert)} contacts. Inserted {inserted}.", "inserted": inserted, "skipped_no_url": skipped_no_url, "skipped_invalid": skipped_invalid, "skipped_dup": skipped_dup}
	except Exception as exc:
		import traceback
		import sys
		traceback.print_exc(file=sys.stdout)
		sys.stdout.flush()
		raise HTTPException(status_code=500, detail=f"Unable to process bulk contacts: {exc}")
	finally:
		_db_put_conn(conn)


@app.get("/campaigns/{campaign_id}/runs")
@app.get("/api/campaigns/{campaign_id}/runs")
def list_campaign_runs(
	request: Request,
	campaign_id: str,
	limit: int = Query(default=25, ge=1, le=200),
) -> dict:
	user_id, is_admin = _get_user_context(request)
	_ensure_campaign_exists(campaign_id, user_id, is_admin)
	if not _db_available or _db_pool is None:
		raise HTTPException(status_code=503, detail="Database is not connected")

	conn = _db_get_conn()
	try:
		with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
			cur.execute("""
				SELECT run_id, status, started_at, finished_at, exit_code, total_leads, processed_leads, duplicates_skipped
				FROM outreach_runs
				WHERE campaign_id = %s
				ORDER BY started_at DESC LIMIT %s
			""", (campaign_id, int(limit)))
			rows = cur.fetchall()

		items = []
		for doc in rows:
			items.append({
				"runId": _safe_trim(doc.get("run_id")),
				"status": _safe_trim(doc.get("status")) or "unknown",
				"startedAt": _safe_trim(doc.get("started_at")),
				"finishedAt": _safe_trim(doc.get("finished_at")),
				"exitCode": doc.get("exit_code"),
				"totalLeads": int(doc.get("total_leads") or 0),
				"processedLeads": int(doc.get("processed_leads") or 0),
				"duplicatesSkipped": int(doc.get("duplicates_skipped") or 0),
			})

		return {"runs": items}
	except Exception as exc:
		raise HTTPException(status_code=500, detail=f"Unable to list campaign runs: {exc}")
	finally:
		_db_put_conn(conn)


@app.post("/outreach/start")
@app.post("/api/outreach/start")
@app.post("/api/start-run")
def start_outreach(request: Request, payload: OutreachStartRequest) -> dict:
	user_id, is_admin = _get_user_context(request)

	if not OUTREACH_SCRIPT.exists():
		raise HTTPException(status_code=500, detail=f"Script not found: {OUTREACH_SCRIPT}")

	requested_csv_path = _resolve_csv_path(payload.csv_path)
	persona_env = _build_persona_env(payload.persona)
	_refresh_process_state()

	with _state_lock:
		if _count_running() >= MAX_CONCURRENT_RUNS:
			raise HTTPException(status_code=409, detail=f"Maximum concurrent runs ({MAX_CONCURRENT_RUNS}) reached. Please wait for a run to finish.")

		persona_payload = payload.persona if isinstance(payload.persona, dict) else {}
		campaign_id = _safe_trim(persona_payload.get("id"))
		campaign_title = _safe_trim(persona_payload.get("title"))

		# Check if this specific campaign already has a running process
		for rid, st in _active_runs.items():
			if st.get("campaign_id") == campaign_id and campaign_id and st.get("process") is not None and st["process"].poll() is None:
				raise HTTPException(status_code=409, detail=f"Campaign '{campaign_title or campaign_id}' already has a run in progress (run_id={rid})")

		# Enforcement: Check campaign-specific daily budget
		success_cap = 0
		if campaign_id:
			try:
				campaign_doc = _ensure_campaign_exists(campaign_id, user_id, is_admin)
				success_cap = int(campaign_doc.get("max_daily_submissions") or 100)
				
				# Count what we've already done today
				already_done = _db_count_campaign_successes_today(campaign_id)
				remaining = max(0, success_cap - already_done)
				
				if remaining <= 0:
					raise HTTPException(
						status_code=403, 
						detail=f"Campaign daily budget reached ({already_done}/{success_cap} successes today). Run skipped."
					)
				
				# Pass the remaining budget to the script
				persona_env["OUTREACH_MAX_DAILY_SUBMISSIONS"] = str(remaining)
				print(f"[Run] Campaign {campaign_id} has {remaining} successes left today (cap={success_cap}).")
				
			except HTTPException:
				raise
			except Exception as exc:
				print(f"[Run] Warning: budget check failed for {campaign_id}: {exc}")

		resume_enabled = bool(payload.resume)
		resume_from_run_id = _safe_trim(payload.resume_from_run_id)
		dedupe_by_domain = bool(payload.dedupe_by_domain)
		max_urls_per_domain = max(1, int(payload.max_urls_per_domain or 1))

		run_id = uuid.uuid4().hex[:12]
		input_arg = requested_csv_path
		total_leads = _count_dataset_items(input_arg)
		duplicates_skipped = 0
		resume_skipped_leads = 0
		social_skipped_leads = 0
		invalid_skipped_leads = 0
		resumed_from_run_id = None
		generated_dataset_path = None
		resume_skip_keys: set[str] = set()
		resume_skip_domains: set[str] = set()

		if resume_enabled:
			resume_source = None
			if resume_from_run_id:
				resume_source = _db_get_run(resume_from_run_id, user_id, is_admin)
			elif campaign_id:
				resume_source = _db_get_latest_resumable_run(campaign_id, user_id)
			else:
				resume_source = _db_get_latest_resumable_run_any(user_id)
			if resume_from_run_id and resume_source is None:
				raise HTTPException(status_code=404, detail=f"Resume run not found: {resume_from_run_id}")
			if resume_source is not None:
				candidate_run_id = _safe_trim(resume_source.get("run_id"))
				if candidate_run_id and candidate_run_id != run_id:
					resume_skip_keys = _db_get_processed_url_keys(candidate_run_id)
					resume_skip_domains = {
						_safe_trim(key).split("/", 1)[0].lower()
						for key in resume_skip_keys
						if _safe_trim(key)
					}
					resume_skip_domains.discard("")
					if resume_skip_keys:
						resumed_from_run_id = candidate_run_id

		replied_skip_keys: set[str] = set()
		if campaign_id and _db_available and _db_pool is not None:
			conn = _db_get_conn()
			try:
				with conn.cursor() as cur:
					cur.execute("SELECT url_key FROM campaign_contacts WHERE campaign_id = %s AND reply_status IN ('interested', 'not_interested')", (campaign_id,))
					for (url_key,) in cur:
						if url_key:
							replied_skip_keys.add(str(url_key).strip())
			except Exception:
				pass
			finally:
				_db_put_conn(conn)

		input_leads: list[dict[str, str]] = []
		if isinstance(payload.leads, list) and payload.leads:
			input_leads = [
				lead.model_dump() if hasattr(lead, "model_dump") else lead.dict() if hasattr(lead, "dict") else vars(lead) if hasattr(lead, "__dict__") else lead
				for lead in payload.leads
			]
		elif requested_csv_path:
			input_leads = _read_leads_from_csv(requested_csv_path)

		if input_leads:
			(
				generated_dataset_path,
				total_leads,
				duplicates_skipped,
				social_skipped_leads,
				resume_skipped_leads,
				invalid_skipped_leads,
				already_submitted_skipped,
				replied_skipped_leads,
			) = _prepare_dataset_from_leads(
				input_leads,
				run_id,
				skip_url_keys=resume_skip_keys,
				skip_domains=resume_skip_domains,
				dedupe_by_domain=dedupe_by_domain,
				max_urls_per_domain=max_urls_per_domain,
				replied_url_keys=replied_skip_keys,
			)
			input_arg = generated_dataset_path
			if already_submitted_skipped > 0:
				print(f"[Dedup] Skipped {already_submitted_skipped} already-submitted contacts")
			if replied_skipped_leads > 0:
				print(f"[Dedup] Skipped {replied_skipped_leads} replied contacts")
		elif requested_csv_path:
			raise HTTPException(status_code=422, detail="No readable leads found in provided dataset")

		cmd = [sys.executable, "-u", str(OUTREACH_SCRIPT)]
		if input_arg:
			cmd.append(input_arg)

		spawn_env = os.environ.copy()
		spawn_env["PYTHONUNBUFFERED"] = "1"
		if persona_env:
			spawn_env.update(persona_env)

		try:
			proc = subprocess.Popen(
				cmd,
				cwd=str(BASE_DIR),
				env=spawn_env,
				stdout=subprocess.PIPE,
				stderr=subprocess.STDOUT,
				text=True,
				bufsize=1,
			)
		except Exception as exc:
			raise HTTPException(status_code=500, detail=f"Failed to start Outreach script: {exc}") from exc

		started_at = _utc_now_iso()
		state = _new_run_state()
		state["process"] = proc
		state["started_at"] = started_at
		state["csv_path"] = requested_csv_path or input_arg
		state["total_leads"] = int(total_leads)
		state["duplicates_skipped"] = int(duplicates_skipped)
		state["resume_skipped_leads"] = int(resume_skipped_leads)
		state["social_skipped_leads"] = int(social_skipped_leads)
		state["resumed_from_run_id"] = resumed_from_run_id or None
		state["generated_csv_path"] = generated_dataset_path
		state["campaign_id"] = campaign_id or None
		state["campaign_title"] = campaign_title or None
		state["user_id"] = user_id or None
		# Store campaign steps for multi-step advancement logic
		state["campaign_steps"] = persona_payload.get("steps") if isinstance(persona_payload, dict) else None
		state["logs"].append(f"[{started_at}] Started: {' '.join(cmd)}")
		if dedupe_by_domain:
			state["logs"].append(f"[{started_at}] Domain-level dedupe enabled: max {max_urls_per_domain} URL(s) per domain")
		if state["duplicates_skipped"] > 0:
			state["logs"].append(f"[{started_at}] Skipped {state['duplicates_skipped']} duplicate lead(s) before execution")
		if state["social_skipped_leads"] > 0:
			state["logs"].append(f"[{started_at}] Skipped {state['social_skipped_leads']} social-media lead(s) before execution")
		if state["resume_skipped_leads"] > 0:
			bookmark = state["resumed_from_run_id"] or "latest bookmark"
			state["logs"].append(f"[{started_at}] Resume bookmark {bookmark} skipped {state['resume_skipped_leads']} processed lead(s)")
		if invalid_skipped_leads > 0:
			state["logs"].append(f"[{started_at}] Skipped {invalid_skipped_leads} invalid lead row(s) before execution")

		_active_runs[run_id] = state

		_db_record_run_start(
			run_id,
			proc.pid,
			state["csv_path"],
			started_at,
			campaign_id=state["campaign_id"],
			campaign_title=state["campaign_title"],
			total_leads=state["total_leads"],
			duplicates_skipped=state["duplicates_skipped"],
			resume_skipped_leads=state["resume_skipped_leads"],
			social_skipped_leads=state["social_skipped_leads"],
			resumed_from_run_id=state["resumed_from_run_id"],
			user_id=user_id
		)

		reader = threading.Thread(target=_stream_process_output, args=(proc, run_id), daemon=True)
		state["reader_thread"] = reader
		reader.start()

		return {
			"status": "started",
			"run_id": run_id,
			"campaign_id": state["campaign_id"],
			"campaign_title": state["campaign_title"],
			"pid": proc.pid,
			"csv_path": state["csv_path"],
			"total_leads": state["total_leads"],
			"processed_leads": 0,
			"duplicates_skipped": state["duplicates_skipped"],
			"resume_skipped_leads": state["resume_skipped_leads"],
			"social_skipped_leads": state["social_skipped_leads"],
			"resumed_from_run_id": state["resumed_from_run_id"],
			"dedupe_by_domain": dedupe_by_domain,
			"max_urls_per_domain": max_urls_per_domain,
			"started_at": started_at,
		}


@app.get("/outreach/status")
@app.get("/api/outreach/status")
@app.get("/api/run-status")
def outreach_status(request: Request) -> dict:
	user_id, is_admin = _get_user_context(request)
	_refresh_process_state()

	query_run_id = request.query_params.get("run_id") or request.query_params.get("runId") or ""
	query_run_id = query_run_id.strip()

	# Find user's active run from in-memory state
	# NOTE: We use in-memory user_id for ownership instead of _db_get_run()
	# because the DB call can fail (empty user_id, pool exhaustion, exception),
	# making the active run invisible and causing the frontend to prematurely
	# mark the run as "completed".
	with _state_lock:
		for rid, st in _active_runs.items():
			if query_run_id and rid != query_run_id:
				continue
			if st.get("process") is not None and (st["process"].poll() is None or (st.get("reader_thread") and st["reader_thread"].is_alive())):
				# Ownership check: admin sees all, otherwise match user_id
				run_owner = st.get("user_id") or ""
				if not is_admin and user_id and run_owner and run_owner != user_id:
					continue
				total = st["total_leads"]
				processed = st["processed_leads"]
				progress = int(round((processed / total) * 100)) if total > 0 else 0
				return {
					"running": True,
					"run_id": rid,
					"campaign_id": st["campaign_id"],
					"campaign_title": st["campaign_title"],
					"pid": st["process"].pid if st["process"] else None,
					"csv_path": st["csv_path"],
					"started_at": st["started_at"],
					"finished_at": st["finished_at"],
					"exit_code": st["exit_code"],
					"total_leads": total,
					"processed_leads": processed,
					"progress": max(0, min(100, progress)),
					"current_lead": st["current_lead"],
					"results": list(st["results"]),
					"duplicates_skipped": st["duplicates_skipped"],
					"resume_skipped_leads": st["resume_skipped_leads"],
					"social_skipped_leads": st["social_skipped_leads"],
					"resumed_from_run_id": st["resumed_from_run_id"],
					"captcha_credits_used_today": 0,
					"captcha_credits_limit": 0,
					"captcha_credits_remaining": 0,
					"status": st.get("status", "running"),
				}

	# DB fallback: use specific run if run_id was requested, otherwise latest
	if query_run_id:
		latest = _db_get_run(query_run_id, user_id, is_admin)
	else:
		latest = _db_get_latest_run(user_id, is_admin)
	if latest is not None:
		db_status = latest.get("status") or "unknown"
		# Auto-correct: if DB says "running" but process is NOT in _active_runs,
		# the run is orphaned (container restart, process crashed, etc.)
		if db_status == "running":
			exit_code = latest.get("exit_code")
			finished_at = latest.get("finished_at")
			if exit_code is not None:
				db_status = "completed" if int(exit_code) == 0 else "failed"
			elif finished_at:
				db_status = "completed"
			else:
				db_status = "stopped"
			# Persist the correction so it doesn't happen again
			_db_update_run_state(
				latest.get("run_id"),
				status=db_status,
				finished_at=finished_at or _utc_now_iso(),
			)
		total_leads = int(latest.get("total_leads") or 0)
		processed_leads = int(latest.get("processed_leads") or 0)
		progress = int(round((processed_leads / total_leads) * 100)) if total_leads > 0 else 0
		
		# Fetch results so the frontend stats don't drop to 0
		db_results = []
		run_id_val = latest.get("run_id")
		if run_id_val:
			conn = _db_get_conn()
			try:
				with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
					cur.execute("""
						SELECT company_name, contact_url, status, submitted, confirmation_msg,
						       captcha_present, captcha_type, captcha_result, form_found,
						       bandwidth_kb, error_detail, fields_filled_data
						FROM outreach_results WHERE run_id = %s
					""", (run_id_val,))
					for row in cur.fetchall():
						captcha_type_val = row["captcha_type"] or "none"
						captcha_result_val = row["captcha_result"] or ""
						captcha_present_val = bool(row["captcha_present"])
						# Build a descriptive captchaStatus string the frontend can parse
						if captcha_present_val:
							captcha_status_str = f"{captcha_type_val} found"
							if captcha_result_val and captcha_result_val not in ("none", ""):
								captcha_status_str += f" - {captcha_result_val}"
						else:
							captcha_status_str = "none"
						db_results.append({
							"companyName": row["company_name"],
							"contactUrl": row["contact_url"],
							"status": row["status"],
							"submitted": row["submitted"],
							"confirmationMsg": row["confirmation_msg"] or "",
							"captchaStatus": captcha_status_str,
							"captchaPresent": captcha_present_val,
							"formFound": bool(row["form_found"]),
							"fieldsFilled": row["fields_filled_data"] or "",
							"bandwidthKb": float(row["bandwidth_kb"] or 0),
							"errorDetail": row["error_detail"] or "",
						})
			except Exception as exc:
				print("[DB Fallback] Error fetching results:", exc)
			finally:
				_db_put_conn(conn)

		return {
			"running": False,
			"run_id": run_id_val,
			"campaign_id": latest.get("campaign_id"),
			"campaign_title": latest.get("campaign_title"),
			"pid": latest.get("pid"),
			"csv_path": latest.get("csv_path"),
			"started_at": latest.get("started_at"),
			"finished_at": latest.get("finished_at"),
			"exit_code": latest.get("exit_code"),
			"total_leads": total_leads,
			"processed_leads": processed_leads,
			"progress": max(0, min(100, progress)),
			"current_lead": "-",
			"results": db_results,
			"duplicates_skipped": int(latest.get("duplicates_skipped") or 0),
			"resume_skipped_leads": int(latest.get("resume_skipped_leads") or 0),
			"social_skipped_leads": int(latest.get("social_skipped_leads") or 0),
			"resumed_from_run_id": latest.get("resumed_from_run_id"),
			"captcha_credits_used_today": 0,
			"captcha_credits_limit": 0,
			"captcha_credits_remaining": 0,
			"status": db_status,
		}

	return {
		"running": False,
		"run_id": None,
		"campaign_id": None,
		"campaign_title": None,
		"pid": None,
		"csv_path": None,
		"started_at": None,
		"finished_at": None,
		"exit_code": None,
		"total_leads": 0,
		"processed_leads": 0,
		"progress": 0,
		"current_lead": "-",
		"results": [],
		"duplicates_skipped": 0,
		"resume_skipped_leads": 0,
		"social_skipped_leads": 0,
		"resumed_from_run_id": None,
		"captcha_credits_used_today": 0,
		"captcha_credits_limit": 0,
		"captcha_credits_remaining": 0,
		"status": "idle",
	}


@app.get("/outreach/logs")
@app.get("/api/outreach/logs")
@app.get("/api/run-logs")
def outreach_logs(
	request: Request,
	tail: int = Query(default=200, ge=1, le=1000),
	run_id: str | None = Query(default=None, description="Optional run_id to fetch historical logs"),
) -> dict:
	user_id, is_admin = _get_user_context(request)
	_refresh_process_state()
	target_run_id = run_id
	fallback_lines: list[str] = []
	with _state_lock:
		if target_run_id is None:
			# Find the user's most recent active run
			for rid, st in _active_runs.items():
				run_doc = _db_get_run(rid, user_id, is_admin)
				if run_doc:
					target_run_id = rid
					fallback_lines = list(st["logs"])[-tail:]
					break
			if target_run_id is None:
				latest_user_run = _db_get_latest_run(user_id, is_admin)
				target_run_id = latest_user_run.get("run_id") if latest_user_run else None
		else:
			# Explicit run_id requested, verify ownership
			run_doc = _db_get_run(target_run_id, user_id, is_admin)
			if not run_doc:
				raise HTTPException(status_code=404, detail=f"Run '{target_run_id}' not found or unauthorized")
			# Check if it's in memory
			if target_run_id in _active_runs:
				fallback_lines = list(_active_runs[target_run_id]["logs"])[-tail:]

	db_lines = _db_get_logs(target_run_id, tail) if target_run_id else []
	lines = db_lines or fallback_lines
	return {
		"run_id": target_run_id,
		"line_count": len(lines),
		"logs": lines,
	}


@app.post("/outreach/stop")
@app.post("/api/outreach/stop")
@app.post("/api/stop-run")
async def stop_outreach(
	request: Request,
	run_id: str | None = Query(default=None, description="Optional run_id to stop a specific run"),
) -> dict:
	user_id, is_admin = _get_user_context(request)
	_refresh_process_state()

	# Read run_id from JSON body if not in query string (frontend sends it in the body)
	body_run_id: str | None = None
	try:
		raw_body = await request.json()
		if isinstance(raw_body, dict):
			body_run_id = str(raw_body.get("run_id") or raw_body.get("runId") or "").strip() or None
	except Exception:
		pass

	with _state_lock:
		target_run_id = run_id or body_run_id
		# If no run_id specified, find the user's running process
		if not target_run_id:
			for rid, st in _active_runs.items():
				if st.get("process") is not None and (st["process"].poll() is None or (st.get("reader_thread") and st["reader_thread"].is_alive())):
					run_owner = st.get("user_id") or ""
					if is_admin or not user_id or (run_owner and run_owner == user_id):
						target_run_id = rid
						break

		if not target_run_id or target_run_id not in _active_runs:
			raise HTTPException(status_code=409, detail="No running Outreach process found")

		state = _active_runs[target_run_id]
		proc = state.get("process")
		if proc is None or (proc.poll() is not None and not (state.get("reader_thread") and state["reader_thread"].is_alive())):
			raise HTTPException(status_code=409, detail="No running Outreach process found")

		# Use the reliable `process` to send the signal, if it is still alive
		if proc.poll() is None:
			try:
				proc.kill()
			except Exception:
				pass
		state["status"] = "stopping"
		state["logs"].append(f"[{_utc_now_iso()}] Stop requested")
		_db_update_run_state(
			target_run_id,
			status="stopping",
			processed_leads=state["processed_leads"],
			total_leads=state["total_leads"],
			duplicates_skipped=state["duplicates_skipped"],
			resume_skipped_leads=state["resume_skipped_leads"],
			social_skipped_leads=state["social_skipped_leads"],
		)
		return {
			"status": "stopping",
			"run_id": target_run_id,
			"pid": proc.pid,
		}


# ─── Contact Lists ──────────────────────────────────────────────────

@app.post("/contact-lists")
@app.post("/api/contact-lists")
def create_contact_list(request: Request, body: dict = Body(...)) -> dict:
	user_id, is_admin = _get_user_context(request)
	if not user_id and not is_admin:
		raise HTTPException(status_code=401, detail="User identification required")

	list_name = body.get("name", "").strip()
	contacts = body.get("contacts", [])

	if not list_name:
		raise HTTPException(status_code=400, detail="List name is required")

	list_id = f"list-{int(time.time())}-{str(uuid.uuid4())[:8]}"
	now = _utc_now_iso()

	conn = _db_get_conn()
	try:
		with conn.cursor() as cur:
			cur.execute("""
				INSERT INTO contact_lists (list_id, name, user_id, created_at, updated_at)
				VALUES (%s, %s, %s, %s, %s)
			""", (list_id, list_name, user_id if not is_admin else user_id or None, now, now))

			if contacts:
				items_to_insert = [
					(list_id, c.get("companyName", "Unknown"), c.get("contactUrl", "").strip())
					for c in contacts if c.get("contactUrl", "").strip()
				]
				if items_to_insert:
					psycopg2.extras.execute_values(cur, """
						INSERT INTO contact_list_items (list_id, company_name, contact_url)
						VALUES %s
					""", items_to_insert)

			conn.commit()

			return {
				"id": list_id,
				"name": list_name,
				"contacts": len(contacts),
				"createdAt": now
			}
	except Exception as exc:
		raise HTTPException(status_code=500, detail=f"Failed to create list: {exc}")
	finally:
		_db_put_conn(conn)


@app.patch("/contact-lists/{list_id}")
@app.patch("/api/contact-lists/{list_id}")
def update_contact_list(request: Request, list_id: str, body: dict = Body(...)) -> dict:
	user_id, is_admin = _get_user_context(request)
	_ensure_record_ownership("contact_lists", "list_id", list_id, user_id, is_admin)

	new_name = body.get("name")
	contacts_to_add = body.get("contacts", [])

	conn = _db_get_conn()
	try:
		with conn.cursor() as cur:
			if new_name is not None:
				cur.execute("UPDATE contact_lists SET name = %s, updated_at = %s WHERE list_id = %s", (new_name.strip(), _utc_now_iso(), list_id))

			if contacts_to_add:
				items_to_insert = [
					(list_id, c.get("companyName", "Unknown"), c.get("contactUrl", "").strip())
					for c in contacts_to_add if c.get("contactUrl", "").strip()
				]
				if items_to_insert:
					psycopg2.extras.execute_values(cur, """
						INSERT INTO contact_list_items (list_id, company_name, contact_url)
						VALUES %s
					""", items_to_insert)

			conn.commit()
			return {"message": "List updated successfully"}
	except Exception as exc:
		raise HTTPException(status_code=500, detail=f"Failed to update list: {exc}")
	finally:
		_db_put_conn(conn)


@app.get("/contact-lists")
@app.get("/api/contact-lists")
def get_contact_lists(request: Request) -> dict:
	user_id, is_admin = _get_user_context(request)
	if not user_id and not is_admin:
		raise HTTPException(status_code=401, detail="User identification required")

	conn = _db_get_conn()
	try:
		with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
			if is_admin:
				cur.execute("""
					SELECT l.list_id, l.name, l.created_at, COUNT(i.id) as contact_count
					FROM contact_lists l
					LEFT JOIN contact_list_items i ON l.list_id = i.list_id
					GROUP BY l.list_id
					ORDER BY l.created_at DESC
				""")
			else:
				cur.execute("""
					SELECT l.list_id, l.name, l.created_at, COUNT(i.id) as contact_count
					FROM contact_lists l
					LEFT JOIN contact_list_items i ON l.list_id = i.list_id
					WHERE l.user_id = %s
					GROUP BY l.list_id
					ORDER BY l.created_at DESC
				""", (user_id,))
			
			rows = cur.fetchall()

			lists = [
				{
					"id": r["list_id"],
					"name": r["name"],
					"createdAt": r["created_at"],
					"contactCount": r["contact_count"]
				} for r in rows
			]

			return {"lists": lists}
	except Exception as exc:
		raise HTTPException(status_code=500, detail=f"Failed to fetch lists: {exc}")
	finally:
		_db_put_conn(conn)


@app.get("/contact-lists/{list_id}")
@app.get("/api/contact-lists/{list_id}")
def get_contact_list_details(request: Request, list_id: str) -> dict:
	user_id, is_admin = _get_user_context(request)
	_ensure_record_ownership("contact_lists", "list_id", list_id, user_id, is_admin)

	conn = _db_get_conn()
	try:
		with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
			cur.execute("SELECT name, created_at FROM contact_lists WHERE list_id = %s", (list_id,))
			list_meta = cur.fetchone()
			if not list_meta:
				raise HTTPException(status_code=404, detail="List not found")

			cur.execute("SELECT company_name, contact_url FROM contact_list_items WHERE list_id = %s", (list_id,))
			contacts = [{"companyName": r["company_name"], "contactUrl": r["contact_url"]} for r in cur.fetchall()]

			return {
				"id": list_id,
				"name": list_meta["name"],
				"createdAt": list_meta["created_at"],
				"contacts": contacts
			}
	except HTTPException:
		raise
	except Exception as exc:
		raise HTTPException(status_code=500, detail=f"Failed to fetch list details: {exc}")
	finally:
		_db_put_conn(conn)


@app.delete("/contact-lists/{list_id}")
@app.delete("/api/contact-lists/{list_id}")
def delete_contact_list(request: Request, list_id: str) -> dict:
	user_id, is_admin = _get_user_context(request)
	_ensure_record_ownership("contact_lists", "list_id", list_id, user_id, is_admin)

	conn = _db_get_conn()
	try:
		with conn.cursor() as cur:
			cur.execute("DELETE FROM contact_lists WHERE list_id = %s", (list_id,))
			conn.commit()
			return {"status": "deleted", "listId": list_id}
	except Exception as exc:
		raise HTTPException(status_code=500, detail=f"Failed to delete list: {exc}")
	finally:
		_db_put_conn(conn)


# ─── User Management (Admin Only) ──────────────────────────────

@app.get("/users")
@app.get("/api/users")
def list_users(request: Request) -> dict:
	"""List all users. Admin only."""
	user_id, is_admin = _get_user_context(request)
	if not is_admin:
		raise HTTPException(status_code=403, detail="Admin access required")

	conn = _db_get_conn()
	try:
		with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
			cur.execute("SELECT id, email, name, is_admin, created_at FROM users ORDER BY created_at DESC")
			rows = [dict(r) for r in cur]
			return {
				"users": [
					{
						"id": str(r["id"]),
						"email": r["email"],
						"name": r["name"] or r["email"].split("@")[0],
						"role": "admin" if r["is_admin"] else "user",
						"isAdmin": bool(r["is_admin"]),
						"createdAt": r["created_at"] or "",
					}
					for r in rows
				]
			}
	finally:
		_db_put_conn(conn)


@app.put("/users/{target_user_id}")
@app.put("/api/users/{target_user_id}")
def update_user_role(request: Request, target_user_id: str, body: dict = Body(...)) -> dict:
	"""Update a user's role. Admin only."""
	user_id, is_admin = _get_user_context(request)
	if not is_admin:
		raise HTTPException(status_code=403, detail="Admin access required")

	new_role = body.get("role", "").lower()
	if new_role not in ("admin", "user"):
		raise HTTPException(status_code=400, detail="Role must be 'admin' or 'user'")

	# Prevent admin from demoting themselves
	if str(target_user_id) == str(user_id) and new_role != "admin":
		raise HTTPException(status_code=400, detail="You cannot demote yourself")

	is_admin_flag = new_role == "admin"

	conn = _db_get_conn()
	try:
		with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
			cur.execute("UPDATE users SET is_admin = %s WHERE id = %s RETURNING id, email, name, is_admin, created_at",
						(is_admin_flag, int(target_user_id)))
			row = cur.fetchone()
			if not row:
				raise HTTPException(status_code=404, detail="User not found")
			conn.commit()
			return {
				"id": str(row["id"]),
				"email": row["email"],
				"name": row["name"] or row["email"].split("@")[0],
				"role": "admin" if row["is_admin"] else "user",
				"isAdmin": bool(row["is_admin"]),
				"createdAt": row["created_at"] or "",
			}
	except HTTPException:
		raise
	except Exception as exc:
		raise HTTPException(status_code=500, detail=f"Failed to update user: {exc}")
	finally:
		_db_put_conn(conn)


@app.delete("/users/{target_user_id}")
@app.delete("/api/users/{target_user_id}")
def delete_user(request: Request, target_user_id: str) -> dict:
	"""Delete a user. Admin only. Cannot delete yourself."""
	user_id, is_admin = _get_user_context(request)
	if not is_admin:
		raise HTTPException(status_code=403, detail="Admin access required")

	if str(target_user_id) == str(user_id):
		raise HTTPException(status_code=400, detail="You cannot delete yourself")

	conn = _db_get_conn()
	try:
		with conn.cursor() as cur:
			cur.execute("DELETE FROM users WHERE id = %s", (int(target_user_id),))
			if cur.rowcount == 0:
				raise HTTPException(status_code=404, detail="User not found")
			conn.commit()
			return {"status": "deleted", "userId": target_user_id}
	except HTTPException:
		raise
	except Exception as exc:
		raise HTTPException(status_code=500, detail=f"Failed to delete user: {exc}")
	finally:
		_db_put_conn(conn)


# ── Campaign & Run Report Endpoints ──────────────────────────────

_REPORT_SQL = """
SELECT
  COUNT(*)                                                        AS total_data,
  COALESCE(SUM(bandwidth_kb) / 1024, 0)                         AS proxy_bandwidth_mb,
  COUNT(*) FILTER (WHERE form_found = TRUE)                      AS contact_form_present,
  COUNT(*) FILTER (WHERE captcha_present = TRUE)                 AS captcha_present_count,
  COUNT(*) FILTER (WHERE form_found = TRUE AND captcha_present = FALSE) AS without_captcha,
  COUNT(*) FILTER (WHERE submitted = 'Yes')                      AS total_successful,
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


def _run_report_query(filter_col: str, filter_val: str) -> dict[str, Any]:
	if not _db_available or _db_pool is None:
		raise HTTPException(status_code=503, detail="Database is not connected")
	conn = _db_get_conn()
	try:
		with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
			cur.execute(f"{_REPORT_SQL} WHERE {filter_col} = %s", (filter_val,))
			row = cur.fetchone()
			if not row:
				return {}
			result = dict(row)
			# Convert Decimal values to float for JSON serialization
			for key, value in result.items():
				if hasattr(value, "as_tuple"):  # Decimal type
					result[key] = float(value)
			return result
	except HTTPException:
		raise
	except Exception as exc:
		raise HTTPException(status_code=500, detail=f"Report query failed: {exc}")
	finally:
		_db_put_conn(conn)


@app.get("/campaigns/{campaign_id}/report")
@app.get("/api/campaigns/{campaign_id}/report")
async def campaign_report(request: Request, campaign_id: str) -> dict:
	"""Get an aggregated report for all results in a campaign."""
	user_id, is_admin = _get_user_context(request)
	_ensure_campaign_exists(campaign_id, user_id=user_id, is_admin=is_admin)
	report = _run_report_query("campaign_id", campaign_id)
	return {"campaign_id": campaign_id, "report": report}


@app.get("/runs/{run_id}/report")
@app.get("/api/runs/{run_id}/report")
async def run_report(request: Request, run_id: str) -> dict:
	"""Get an aggregated report for all results in a specific run."""
	user_id, is_admin = _get_user_context(request)
	run = _db_get_run(run_id, user_id=user_id, is_admin=is_admin)
	if not run:
		raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")
	report = _run_report_query("run_id", run_id)
	return {"run_id": run_id, "report": report}


if __name__ == "__main__":
	import uvicorn

	uvicorn.run("Back:app", host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
