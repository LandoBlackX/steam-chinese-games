import os
import sys
import json
import sqlite3
from pathlib import Path
from datetime import datetime
import time
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE_DIR = Path(os.environ.get('GITHUB_WORKSPACE', Path(__file__).parent.parent))
DATA_DIR = BASE_DIR / 'data'
DATA_DIR.mkdir(exist_ok=True, parents=True)
DB_PATH = DATA_DIR / "app_list.db"
OUTPUT_PATH = DATA_DIR / "output.json"

DEBUG_MODE = os.getenv("DEBUG_MODE", "false").lower() == "true"

class SteamRateLimiter:
    def __init__(self, requests_per_minute=200):
        self.requests_per_minute = requests_per_minute
        self.request_timestamps = []
        self.last_response_time = 0

    def can_make_request(self):
        current_time = time.time()
        self.request_timestamps = [t for t in self.request_timestamps if current_time - t < 60]
        if len(self.request_timestamps) < self.requests_per_minute:
            self.request_timestamps.append(current_time)
            return True
        return False

    def wait_for_slot(self):
        while not self.can_make_request():
            time.sleep(1)
        if self.last_response_time > 0.5:
            time.sleep(0.2)

    def update_response_time(self, response_time):
        self.last_response_time = response_time

def log(message, level="info"):
    if level == "debug" and not DEBUG_MODE:
        return
    print(f"[{datetime.now().isoformat()}] {message}", file=sys.stderr, flush=True)

def load_appids_from_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT appid FROM apps WHERE status = FALSE")
    appids = [row[0] for row in cursor.fetchall()]
    conn.close()
    log(f"从数据库加载 {len(appids)} 个待处理 AppID")
    return appids

def update_status(appid):
    """更新 AppID status 为 TRUE（并发安全，用单独 conn）"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("UPDATE apps SET status = true WHERE appid = ?", (appid,))
        conn.commit()
        conn.close()
        log(f"标记 AppID {appid} status = TRUE", level="debug")
    except Exception as e:
        log(f"更新 status 失败 for {appid}: {e}", level="info")

def check_app_details(appid, rate_limiter):
    url = f"https://store.steampowered.com/api/appdetails?appids={appid}"
    max_attempts = 3
    for attempt in range(max_attempts):
        rate_limiter.wait_for_slot()
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
            }
            start = time.time()
            response = requests.get(url, headers=headers, timeout=15)
            duration = time.time() - start
            rate_limiter.update_response_time(duration)
            response.raise_for_status()
            data = response.json()
            appid_str = str(appid)
            game_data = data.get(appid_str, {})
            if game_data.get("success", False):
                game_info = game_data["data"]
                app_type = game_info.get("type", "unknown")
                log(f"AppID {appid} 类型: {app_type} | 响应时间: {duration:.2f}秒", level="debug")
                return {appid_str: app_type}
            else:
                log(f"AppID {appid} API 返回 success: false (尝试 {attempt + 1}/{max_attempts})", level="info")
                if attempt < max_attempts - 1:
                    time.sleep(5)
                continue
        except requests.exceptions.RequestException as e:
            if "429" in str(e):
                log(f"429 触发于 AppID {appid}，等待 300 秒", level="info")
                time.sleep(300)
                continue
            else:
                log(f"请求 AppID {appid} 失败: {e} (尝试 {attempt + 1}/{max_attempts})", level="info")
                if attempt < max_attempts - 1:
                    time.sleep(5)
                continue
    # 失败也标记 processed
    update_status(appid)
    return None

def save_output(data):
    try:
        with open(OUTPUT_PATH, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        file_size = OUTPUT_PATH.stat().st_size / (1024 * 1024)
        log(f"output.json 已保存，大小: {file_size:.2f} MB")
    except Exception as e:
        log(f"保存 output.json 失败: {str(e)}")
        raise

def main():
    log("Get App Details 脚本启动")
    appids = load_appids_from_db()
    if not appids:
        log("无 AppID 待处理，跳过")
        return

    # 去掉小批限，全量处理（生产模式）
    log(f"开始处理 {len(appids)} 个 AppID")

    rate_limiter = SteamRateLimiter(requests_per_minute=200)
    output_data = {}
    success_count = 0
    failure_count = 0

    # 使用线程池并发（max_workers=10，避免过载）
    with ThreadPoolExecutor(max_workers=10) as executor:
        future_to_appid = {executor.submit(check_app_details, appid, rate_limiter): appid for appid in appids}
        for future in as_completed(future_to_appid):
            appid = future_to_appid[future]
            try:
                result = future.result()
                if result:
                    output_data.update(result)
                    success_count += 1
                else:
                    failure_count += 1
            except Exception as e:
                log(f"处理 AppID {appid} 时异常: {e}", level="info")
                failure_count += 1
            # 并发后立即更新 status
            update_status(appid)

    save_output(output_data)
    log(f"处理完成！成功: {success_count}, 失败: {failure_count}")
    log(f"output.json 中游戏类 AppID 数量: {sum(1 for v in output_data.values() if v == 'game')}")

if __name__ == "__main__":
    main()
