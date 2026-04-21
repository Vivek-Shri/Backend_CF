# outreach/sheets.py
import asyncio
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from .config import SPREADSHEET_ID, CREDS_FILE, SHEET_HEADERS
from .utils import mask_secret

sheet_lock = asyncio.Lock()

def get_gspread_client():
    try:
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds = ServiceAccountCredentials.from_json_keyfile_name(CREDS_FILE, scope)
        return gspread.authorize(creds)
    except Exception as e:
        print(f" [Sheets] Error initializing client: {e}")
        return None

async def init_sheet():
    async with sheet_lock:
        client = await asyncio.to_thread(get_gspread_client)
        if not client: return None
        try:
            sh = client.open_by_key(SPREADSHEET_ID)
            wks = sh.get_worksheet(0)
            # Ensure headers exist
            headers = wks.row_values(1)
            if not headers:
                wks.append_row(SHEET_HEADERS)
            return wks
        except Exception as e:
            print(f" [Sheets] Error opening sheet: {e}")
            return None

async def append_to_sheet(wks, row_data: list):
    if not wks: return
    async with sheet_lock:
        try:
            await asyncio.to_thread(wks.append_row, row_data)
        except Exception as e:
            print(f" [Sheets] Error appending row: {e}")
