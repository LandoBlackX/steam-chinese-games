import os
import sys
import json
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta
import time

BASE_DIR = Path(os.environ.get('GITHUB_WORKSPACE', Path(__file__).parent.parent))
DATA_DIR = BASE_DIR / 'data'
DATA_DIR.mkdir(exist_ok=True, parents=True)
DB_PATH = DATA_DIR / "app_list.db"
INVALID_LOG_PATH = DATA_DIR / "invalid_appids.log"  # 新增日志文件

class SteamRateLimiter:
    # ...（原有 SteamRateLimiter 类保持不变）...

def log(message):
    print(f"[{datetime.now().isoformat()}] {message}", file=sys.stderr, flush=True)

def log_failed_appid(appid, reason):
    # ...（原有 log_failed_appid 函数保持不变）...

def init_data_structure():
    # ...（原有 init_data_structure 函数保持不变）...

def safe_load_json(file):
    # ...（原有 safe_load_json 函数保持不变）...

def load_game_appids(existing_chinese, existing_cards, conn, cursor):
    output_path = DATA_DIR / "output.json"
    if not output_path.exists():
        log("错误：output.json 文件不存在")
        return []

    try:
        with open(output_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            if not isinstance(data, dict):
                log("错误：output.json 内容不是有效的字典")
                return []
            
            appids = []
            thirty_days_ago = datetime.utcnow() - timedelta(days=30)
            invalid_appids = []  # 记录无效 AppID
            
            # 获取数据库中所有有效 AppID
            cursor.execute("SELECT appid FROM apps")
            db_appids = set(row[0] for row in cursor.fetchall())
            
            for appid_str, app_info in data.items():
                if app_info == "game":
                    appid_int = int(appid_str)
                    
                    # 检查 AppID 是否存在于数据库
                    if appid_int not in db_appids:
                        invalid_appids.append(appid_int)
                        continue  # 跳过无效 AppID
                    
                    # 原有逻辑：检查 scraper_status 和 last_checked
                    cursor.execute("SELECT scraper_status FROM apps WHERE appid = ?", (appid_int,))
                    scraper_status_row = cursor.fetchone()
                    if scraper_status_row and scraper_status_row[0]:
                        continue
                    
                    existing_c = existing_chinese["games"].get(appid_str, {})
                    existing_card = existing_cards["games"].get(appid_str, {})
                    last_checked = existing_c.get("last_checked") or existing_card.get("last_checked")
                    if not last_checked or datetime.fromisoformat(last_checked) < thirty_days_ago:
                        appids.append(appid_int)

            # 将无效 AppID 记录到日志文件
            if invalid_appids:
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                log_entry = f"[{timestamp}] 无效 AppID: {invalid_appids}\n"
                with open(INVALID_LOG_PATH, 'a', encoding='utf-8') as f:
                    f.write(log_entry)
                log(f"发现 {len(invalid_appids)} 个无效 AppID，已记录至 {INVALID_LOG_PATH}")

            appids.sort(reverse=False)
            log(f"从 output.json 加载到 {len(appids)} 个待处理游戏类 AppID")
            return appids[:100]  # 每次处理 100 个

    except Exception as e:
        log(f"加载 output.json 失败: {str(e)}")
        return []

def check_game(appid, rate_limiter):
    # ...（原有 check_game 函数保持不变）...

def save_data(data, file_path):
    # ...（原有 save_data 函数保持不变）...

def main():
    log("脚本启动")
    chinese_data = safe_load_json(DATA_DIR / "chinese_games.json")
    card_data = safe_load_json(DATA_DIR / "card_games.json")
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS apps (
        appid INTEGER PRIMARY KEY,
        status BOOLEAN DEFAULT FALSE,
        scraper_status BOOLEAN DEFAULT FALSE,
        retry_count INTEGER DEFAULT 0
    )
    ''')
    conn.commit()
    
    test_appids = load_game_appids(chinese_data, card_data, conn, cursor)
    if not test_appids:
        log("没有需要处理的新 AppID，终止执行")
        cursor.close()
        conn.close()
        return
    
    log(f"开始处理 {len(test_appids)} 个 AppID")
    rate_limiter = SteamRateLimiter(requests_per_minute=200)
    results = []
    success_count = 0
    failure_count = 0
    
    for appid in test_appids:
        result = check_game(appid, rate_limiter)
        if result:
            results.append(result)
            success_count += 1
            cursor.execute("UPDATE apps SET scraper_status = true, retry_count = 0 WHERE appid = ?", (appid,))
        else:
            failure_count += 1
            cursor.execute("UPDATE apps SET retry_count = retry_count + 1 WHERE appid = ?", (appid,))
            cursor.execute("SELECT retry_count FROM apps WHERE appid = ?", (appid,))
            retry_count = cursor.fetchone()[0]
            if retry_count >= 3:
                cursor.execute("UPDATE apps SET scraper_status = true WHERE appid = ?", (appid,))
                log(f"AppID {appid} 重试次数达到 3 次，标记为已处理")
        conn.commit()
    
    log(f"处理完成！成功: {success_count}, 失败: {failure_count}")
    
    updated = False
    for result in results:
        if result:
            appid_str = str(result["appid"])
            if result["supports_chinese"]:
                chinese_data["games"][appid_str] = result
                updated = True
            if result["supports_cards"]:
                card_data["games"][appid_str] = result
                updated = True
    
    if updated:
        timestamp = datetime.utcnow().isoformat()
        chinese_data["_metadata"]["updated"] = timestamp
        card_data["_metadata"]["updated"] = timestamp
        save_data(chinese_data, DATA_DIR / "chinese_games.json")
        save_data(card_data, DATA_DIR / "card_games.json")
    
    log(f"完成！累计中文游戏: {len(chinese_data['games']}")
    log(f"完成！累计卡牌游戏: {len(card_data['games']}")
    
    if os.getenv("GITHUB_ACTIONS") == "true":
        with open(os.getenv("GITHUB_OUTPUT"), 'a') as f:
            f.write(f"processed={len(test_appids)}\n")
            f.write(f"new_chinese={len(chinese_data['games'])}\n")
            f.write(f"new_cards={len(card_data['games'])}\n")
    
    cursor.close()
    conn.close()

if __name__ == "__main__":
    main()
