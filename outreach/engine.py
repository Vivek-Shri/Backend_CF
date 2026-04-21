# outreach/engine.py
import asyncio
import sys
import os
import signal
import json
from datetime import datetime
from playwright.async_api import async_playwright

import psycopg2
import psycopg2.extras
from .config import PARALLEL_COUNT, SPREADSHEET_ID, PROXY_LIST
from .leads import load_leads_from_env, load_leads_from_path, load_bookmark, save_bookmark
from .proxies import get_proxy_for_worker
from .sheets import init_sheet
from .worker import OutreachWorker
from .tracking import token_tracker
from .db import fetch_campaign_data

# _fetch_campaign_settings removed, now using db.fetch_campaign_data

STOP_FLAG = asyncio.Event()

def signal_handler(sig, frame):
    print("\n [Engine] Interrupt received, stopping...")
    STOP_FLAG.set()

async def main():
    # Register signal handlers
    if sys.platform != "win32":
        try:
            loop = asyncio.get_running_loop()
            for s in (signal.SIGHUP, signal.SIGTERM, signal.SIGINT):
                loop.add_signal_handler(s, lambda: STOP_FLAG.set())
        except Exception:
            pass
    else:
        # On Windows, signal handler is limited
        try:
            signal.signal(signal.SIGINT, signal_handler)
        except Exception:
            pass

    # print("\n" + "="*65)
    # print("  MODULAR OUTREACH ENGINE v1.0")
    # print("="*65)
    
    # 1. Load Leads
    csv_path = sys.argv[1] if len(sys.argv) > 1 else None
    campaign_id = sys.argv[2] if len(sys.argv) > 2 else None

    if csv_path:
        leads = load_leads_from_path(csv_path)
    else:
        leads = load_leads_from_env()

    if not leads:
        print(" [Engine] No leads found. Exiting.")
        return

    # print(f" [Engine] Loaded {len(leads)} leads.")

    # 2. Setup Sheets
    wks = await init_sheet()

    # 3. Fetch Campaign Settings
    campaign_data = fetch_campaign_data(campaign_id)
    search_enabled = campaign_data.get("search_for_form", False)
    print(f" [Engine] Campaign Settings: search_for_form={search_enabled}")

    # 4. Resume / Bookmark
    processed_indices = load_bookmark()
    pending_leads = [(i, lead) for i, lead in enumerate(leads) if i not in processed_indices]
    
    if not pending_leads:
        print(" [Engine] All leads already processed.")
        return

    print(f" [Engine] Starting Run: {len(pending_leads)} leads pending.")

    # 5. Worker Pool
    queue = asyncio.Queue()
    for item in pending_leads:
        await queue.put(item)

    async def worker_loop(worker_index):
        proxy_config, proxy_label = get_proxy_for_worker(worker_index)
        worker = OutreachWorker(
            worker_index, 
            campaign_id=campaign_id,
            proxy=proxy_config, 
            wks=wks, 
            search_enabled=search_enabled
        )
        
        while not queue.empty() and not STOP_FLAG.is_set():
            try:
                idx, lead = queue.get_nowait()
            except asyncio.QueueEmpty:
                break
                
            try:
                result = await worker.run(lead)
                # Print result in the format Back.py expects
                if result:
                    print(f"[RESULT] {json.dumps(result)}")
                
                processed_indices.add(idx)
                save_bookmark(processed_indices)
            except Exception as e:
                print(f" [Worker {worker_index}] !! Process Error (Lead {idx}): {e}")
            finally:
                queue.task_done()

    # Create and run workers
    num_workers = min(PARALLEL_COUNT, len(pending_leads))
    tasks = [asyncio.create_task(worker_loop(i)) for i in range(num_workers)]
    
    await asyncio.gather(*tasks)

    print("\n [Engine] RUN COMPLETE")
    token_tracker.print_summary()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
