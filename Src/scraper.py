import os
import sys
import requests
import json
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta
import time

BASE_DIR = Path(os.environ.get('GITHUB_WORKSPACE', Path(__file__).parent.parent))
DATA_DIR = BASE_DIR / 'data'
DATA_DIR.mkdir(exist_ok=True, parents=True)

DB_PATH = DATA_DIR / "app_list.db"

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

def init_data_structure():
    return {
        "_metadata": {
            "created": datetime.utcnow().isoformat(),
            "updated": None,
            "version": 1
        },
        "games": {}
    }

def safe_load_json(file):
    try:
        if file.exists() and file.stat().st_size > 0:
            with open(file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                data.setdefault("_metadata", {})
                data.setdefault("games", {})
                return data
    except Exception as e:
        log(f"Failed to load {file}: {str(e)}")
    return init_data_structure()

def load_game_appids(existing_chinese, existing_cards, conn, cursor):
    thirty_days_ago = (datetime.now() - timedelta(days=30)).isoformat()
    cursor.execute("""
        SELECT appid FROM apps 
        WHERE 
            status = true
            AND is_game = true
            AND scraper_status = false
            AND last_updated < ?
        ORDER BY appid
        LIMIT 199
    """, (thirty_days_ago,))
    appids = [row[0] for row in cursor.fetchall()]
    log(f"Loaded {len(appids)} game AppIDs (not updated in 30 days)")
    return appids

def check_game(appid, rate_limiter):
    url = f"https://store.steampowered.com/api/appdetails?appids={appid}&l=schinese"
    rate_limiter.wait_for_slot()
    try:
        start = time.time()
        response = requests.get(url, timeout=15)
        duration = time.time() - start
        rate_limiter.update_response_time(duration)
        response.raise_for_status()
        data = response.json()
        appid_str = str(appid)
        game_data = data.get(appid_str, {})
        if game_data.get("success", False):
            game_info = game_data["data"]
            langs = game_info.get("supported_languages", "") + "|" + game_info.get("languages", "")
            chinese_keywords = ['schinese', 'tchinese', '中文', '简体', '繁体', 'Chinese']
            has_chinese = any(kw.lower() in langs.lower() for kw in chinese_keywords)
            has_cards = any(cat.get("id") == 29 for cat in game_info.get("categories", []))
            log(f"Game {appid}: {'Chinese' if has_chinese else 'No Chinese'} | {'Cards' if has_cards else 'No Cards'} | {duration:.2f}s")
            return {
                "appid": appid,
                "name": game_info.get("name", f"Unknown_{appid}"),
                "type": "game",
                "supports_chinese": has_chinese,
                "supports_cards": has_cards,
                "last_checked": datetime.utcnow().isoformat()
            }
        else:
            log(f"Failed AppID: {appid} (API success=false)")
            log_failed_appid(appid, "API success=false")
            return None
    except requests.exceptions.RequestException as e:
        if "429" in str(e):
            log("Rate limit hit, pausing for 5 minutes...")
            time.sleep(300)
            return None
        else:
            log(f"Request failed for AppID: {appid}: {e}")
            log_failed_appid(appid, str(e))
            return None

def save_data(data, file_path):
    try:
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        log(f"Data saved to {file_path}")
    except Exception as e:
        log(f"Save failed: {str(e)}")
        raise

def main():
    log("Starting scraper...")
    chinese_data = safe_load_json(DATA_DIR / "chinese_games.json")
    card_data = safe_load_json(DATA_DIR / "card_games.json")
    
    conn = sqlite3.connect(DB_PATH)
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
    
    test_appids = load_game_appids(chinese_data, card_data, conn, cursor)
    if not test_appids:
        log("No new AppIDs to process")
        cursor.close()
        conn.close()
        return
    
    log(f"Processing {len(test_appids)} AppIDs")
    rate_limiter = SteamRateLimiter(requests_per_minute=200)
    updated = False

    for appid in test_appids:
        result = check_game(appid, rate_limiter)
        if result:
            if result["supports_chinese"]:
                chinese_data["games"][str(appid)] = result
                updated = True
            if result["supports_cards"]:
                card_data["games"][str(appid)] = result
                updated = True
            cursor.execute("""
                UPDATE apps 
                SET scraper_status = true, 
                    last_updated = ?
                WHERE appid = ?
            """, (datetime.now().isoformat(), appid))
            conn.commit()

    if updated:
        timestamp = datetime.utcnow().isoformat()
        chinese_data["_metadata"]["updated"] = timestamp
        card_data["_metadata"]["updated"] = timestamp
        save_data(chinese_data, DATA_DIR / "chinese_games.json")
        save_data(card_data, DATA_DIR / "card_games.json")
    
    log(f"Total Chinese games: {len(chinese_data['games'])}")
    log(f"Total card games: {len(card_data['games'])}")

    if os.getenv("GITHUB_ACTIONS") == "true":
        with open(os.getenv("GITHUB_OUTPUT"), 'a') as f:
            f.write(f"processed={len(test_appids)}\n")
            f.write(f"new_chinese={len(chinese_data['games'])}\n")
            f.write(f"new_cards={len(card_data['games'])}\n")

    cursor.close()
    conn.close()

if __name__ == "__main__":
    main()
