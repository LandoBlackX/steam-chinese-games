import json
import os
import sqlite3
import warnings
import sys
from pathlib import Path
import requests
from urllib3.exceptions import InsecureRequestWarning
import time
from datetime import datetime

warnings.simplefilter('ignore', InsecureRequestWarning)

BASE_DIR = Path(os.environ.get('GITHUB_WORKSPACE', Path(__file__).parent.parent))
DATA_DIR = BASE_DIR / 'data'
DATA_DIR.mkdir(exist_ok=True, parents=True)

db_path = DATA_DIR / 'app_list.db'
getDetails_URL = "https://store.steampowered.com/api/appdetails?l=english&appids="
output_file = DATA_DIR / 'output.json'

class SteamRateLimiter:
    def __init__(self, requests_per_minute=200):
        self.requests_per_minute = requests_per_minute
        self.request_timestamps = []
        self.last_response_time = 0

    def can_make_request(self):
        current_time = time.time()
        self.request_timestamps = [t for t in self.request_timestamps if current_time - t < 60]
        return len(self.request_timestamps) < self.requests_per_minute

    def wait_for_slot(self):
        while not self.can_make_request():
            time.sleep(1)
        if self.last_response_time > 0.5:
            time.sleep(0.2)

    def update_response_time(self, response_time):
        self.last_response_time = response_time

def log(message):
    print(f"[{datetime.now().isoformat()}] {message}", file=sys.stderr, flush=True)

def log_failed_appid(appid, reason):
    failed_file = DATA_DIR / 'failed_appids.json'
    failed_data = {}
    if failed_file.exists():
        with open(failed_file, 'r', encoding='utf-8') as f:
            failed_data = json.load(f)
    failed_data[str(appid)] = reason
    with open(failed_file, 'w', encoding='utf-8') as f:
        json.dump(failed_data, f, indent=2, ensure_ascii=False)

def write_results_to_file(results):
    if output_file.exists():
        with open(output_file, 'r', encoding='utf-8') as f:
            existing_results = json.load(f)
    else:
        existing_results = {}
    existing_results.update(results)
    sorted_results = {str(k): v for k, v in sorted(existing_results.items(), key=lambda item: int(item[0]))}
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(sorted_results, f, ensure_ascii=False, indent=4)
    log("Results written to output.json (sorted by AppID)")

def update_status(conn, cursor, appid, app_type):
    is_game = 1 if app_type == 'game' else 0
    current_time = datetime.now().isoformat()
    cursor.execute("""
        UPDATE apps SET 
            status = true,
            is_game = ?,
            last_updated = ?
        WHERE appid = ?
    """, (is_game, current_time, appid))
    conn.commit()

def check_app(appid, rate_limiter):
    url = f"{getDetails_URL}{appid}"
    rate_limiter.wait_for_slot()
    try:
        start = time.time()
        response = requests.get(url, verify=False, timeout=15)
        duration = time.time() - start
        rate_limiter.update_response_time(duration)
        response.raise_for_status()
        data = response.json()
        appid_str = str(appid)
        if data.get(appid_str, {}).get('success'):
            app_data = data[appid_str]['data']
            app_type = app_data.get('type', 'Unknown')
            log(f"AppID: {appid}, Type: {app_type}, Response: {duration:.2f}s")
            return appid, app_type
        else:
            reason = f"API response: {data.get(appid_str, 'No data')}"
            log(f"Failed AppID: {appid}, {reason}")
            log_failed_appid(appid, reason)
            return appid, None
    except requests.exceptions.RequestException as e:
        if "429" in str(e):
            log("Rate limit hit, pausing for 5 minutes...")
            time.sleep(300)
            return appid, None
        else:
            log(f"Request failed for AppID: {appid}: {e}")
            log_failed_appid(appid, str(e))
            return appid, None

def main():
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute('''
    CREATE TABLE IF NOT EXISTS apps (
        appid INTEGER PRIMARY KEY,
        status BOOLEAN DEFAULT FALSE,
        scraper_status BOOLEAN DEFAULT FALSE,
        is_game BOOLEAN DEFAULT FALSE,
        last_updated TEXT DEFAULT "2020-01-01T00:00:00"
    )
    ''')
    conn.commit()

    rows = cursor.execute("SELECT appid FROM apps WHERE status = false").fetchall()
    appids = [row[0] for row in rows[:1]]  # Process 1 AppID per run
    if not appids:
        log("No new AppIDs to process")
        cursor.close()
        conn.close()
        return

    log(f"Processing {len(appids)} AppID(s)")
    rate_limiter = SteamRateLimiter(requests_per_minute=200)
    results = {}

    for appid in appids:
        appid, app_type = check_app(appid, rate_limiter)
        if app_type:
            results[appid] = app_type
            update_status(conn, cursor, appid, app_type)

    write_results_to_file(results)
    cursor.close()
    conn.close()
    log("Waiting 5 seconds before next run...")
    time.sleep(5)

if __name__ == "__main__":
    main()
