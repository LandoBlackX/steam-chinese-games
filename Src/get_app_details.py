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
    # (1) 加载现有内容
    if output_file.exists():
        with open(output_file, 'r', encoding='utf-8') as f:
            existing_results = json.load(f)
    else:
        existing_results = {}

    # (2) 更新内容
    existing_results.update(results)

    # (3) 按 AppID 升序排序
    sorted_results = {str(k): v for k, v in sorted(
        existing_results.items(),
        key=lambda item: int(item[0])  # 按 AppID 的数值升序排序
    )}

    # (4) 写回文件
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(sorted_results, f, ensure_ascii=False, indent=4)

    log("已将结果写入 output.json 并按 AppID 升序排序")

def update_status(conn, cursor, appid):
    cursor.execute("UPDATE apps SET status = true WHERE appid = ?", (appid,))
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
            log(f"AppID: {appid}, 类型: {app_type}, 响应时间: {duration:.2f}秒")
            return appid, app_type
        else:
            reason = f"API 返回: {data.get(appid_str, '无数据')}"
            log(f"获取 AppID: {appid} 的详情失败，{reason}")
            log_failed_appid(appid, reason)
            return appid, None
    except requests.exceptions.RequestException as e:
        if "429" in str(e):
            log(f"触发 429 错误，暂停 5 分钟后重试...")
            time.sleep(300)
            return appid, None
        else:
            log(f"请求 AppID: {appid} 失败: {e}")
            log_failed_appid(appid, str(e))
            return appid, None

def main():
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # 创建数据库表（如果不存在）
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS apps (
        appid INTEGER PRIMARY KEY,
        status BOOLEAN DEFAULT FALSE,
        scraper_status BOOLEAN DEFAULT FALSE
    )
    ''')
    conn.commit()

    # 查询未处理的 AppID
    rows = cursor.execute("SELECT appid FROM apps WHERE status = false").fetchall()
    appids = [row[0] for row in rows[:100]]  # 每次处理 100 个 AppID
    if not appids:
        log("没有需要处理的新 AppID，终止执行")
        cursor.close()
        conn.close()
        return

    log(f"开始处理 {len(appids)} 个 AppID")
    rate_limiter = SteamRateLimiter(requests_per_minute=200)
    results = {}
    success_count = 0
    failure_count = 0

    # 遍历并处理每个 AppID
    for appid in appids:
        appid, app_type = check_app(appid, rate_limiter)
        update_status(conn, cursor, appid)
        if app_type:
            results[appid] = app_type
            success_count += 1
        else:
            failure_count += 1

    log(f"处理完成！成功: {success_count}, 失败: {failure_count}")
    write_results_to_file(results)
    cursor.close()
    conn.close()

    log("完成 get_app_details.py，等待 5 秒后继续...")
    time.sleep(5)

if __name__ == "__main__":
    main()
