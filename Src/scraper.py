import os
import sys
import json
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta
import time
import requests

BASE_DIR = Path(os.environ.get('GITHUB_WORKSPACE', Path(__file__).parent.parent))
DATA_DIR = BASE_DIR / 'data'
DATA_DIR.mkdir(exist_ok=True, parents=True)
DB_PATH = DATA_DIR / "app_list.db"
INVALID_LOG_PATH = DATA_DIR / "invalid_appids.json"

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

def safe_load_invalid_appids():
    try:
        if INVALID_LOG_PATH.exists() and INVALID_LOG_PATH.stat().st_size > 0:
            with open(INVALID_LOG_PATH, 'r', encoding='utf-8') as f:
                data = json.load(f)
                cutoff_time = (datetime.now() - timedelta(days=30)).isoformat()
                data["invalid_appids"] = [
                    entry for entry in data.get("invalid_appids", [])
                    if entry["timestamp"] >= cutoff_time
                ]
                return data
    except Exception as e:
        log(f"加载 {INVALID_LOG_PATH} 失败: {str(e)}")
    return {"invalid_appids": []}

def log_failed_appid(appid, reason):
    failed_file = DATA_DIR / 'failed_appids.json'
    invalid_data = safe_load_invalid_appids()
    recorded_appids = {entry["appid"] for entry in invalid_data.get("invalid_appids", [])}
    
    if appid not in recorded_appids:
        invalid_data["invalid_appids"].append({
            "appid": appid,
            "reason": reason,
            "timestamp": datetime.now().isoformat()
        })
        with open(failed_file, 'w', encoding='utf-8') as f:
            json.dump(invalid_data, f, indent=2, ensure_ascii=False)
        log(f"记录新无效 AppID {appid} 到 {failed_file}", level="debug")

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
        log(f"加载 {file} 失败: {str(e)}")
    return init_data_structure()

def initialize_last_checked(conn, cursor, chinese_data, card_data):
    """从 chinese_games.json 和 card_games.json 初始化 apps 表的 last_checked 和 scraper_status"""
    log("初始化 apps 表的 last_checked 字段...")
    cursor.execute("SELECT COUNT(*) FROM apps WHERE last_checked IS NULL")
    null_count = cursor.fetchone()[0]
    if null_count == 0:
        log("所有 AppID 已初始化 last_checked，跳过初始化")
        return
    
    both_chinese_and_cards = set(chinese_data["games"].keys()) & set(card_data["games"].keys())
    default_timestamp = datetime.utcnow().isoformat()
    
    processed_appids = set()
    for appid_str in chinese_data["games"]:
        appid = int(appid_str)
        last_checked = chinese_data["games"][appid_str].get("last_checked", default_timestamp)
        try:
            datetime.fromisoformat(last_checked)
            scraper_status = True if appid_str in both_chinese_and_cards else False
            cursor.execute(
                "UPDATE apps SET last_checked = ?, scraper_status = ? WHERE appid = ?",
                (last_checked, scraper_status, appid)
            )
            processed_appids.add(appid)
        except ValueError:
            log(f"中文游戏 AppID {appid_str} 的 last_checked 格式错误: {last_checked}, 使用默认时间", level="debug")
            cursor.execute(
                "UPDATE apps SET last_checked = ?, scraper_status = ? WHERE appid = ?",
                (default_timestamp, scraper_status, appid)
            )
            processed_appids.add(appid)
    
    for appid_str in card_data["games"]:
        appid = int(appid_str)
        if appid not in processed_appids:
            last_checked = card_data["games"][appid_str].get("last_checked", default_timestamp)
            try:
                datetime.fromisoformat(last_checked)
                scraper_status = True if appid_str in both_chinese_and_cards else False
                cursor.execute(
                    "UPDATE apps SET last_checked = ?, scraper_status = ? WHERE appid = ?",
                    (last_checked, scraper_status, appid)
                )
            except ValueError:
                log(f"卡牌游戏 AppID {appid_str} 的 last_checked 格式错误: {last_checked}, 使用默认时间", level="debug")
                cursor.execute(
                    "UPDATE apps SET last_checked = ?, scraper_status = ? WHERE appid = ?",
                    (default_timestamp, scraper_status, appid)
                )
    
    conn.commit()
    cursor.execute("SELECT COUNT(*) FROM apps WHERE last_checked IS NOT NULL")
    initialized_count = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM apps WHERE scraper_status = TRUE")
    exempt_count = cursor.fetchone()[0]
    log(f"已为 {initialized_count} 个 AppID 初始化 last_checked 字段")
    log(f"标记 {exempt_count} 个既有中文又有卡牌的 AppID 为 scraper_status = TRUE")

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
            
            game_appids = [appid_str for appid_str, app_info in data.items() if app_info == "game"]
            cursor.execute(
                f"SELECT COUNT(*) FROM apps WHERE scraper_status = FALSE AND appid IN ({','.join(['?'] * len(game_appids))})",
                [int(appid) for appid in game_appids]
            )
            pending_count = cursor.fetchone()[0]
            log(f"数据库中待处理的游戏类 AppID 数量 (scraper_status = FALSE): {pending_count}")
            
            appids = []
            recheck_period = datetime.utcnow() - timedelta(days=90)
            invalid_appids = []
            invalid_data = safe_load_invalid_appids()
            recorded_appids = {entry["appid"] for entry in invalid_data.get("invalid_appids", [])}
            
            cursor.execute("SELECT appid FROM apps")
            db_appids = set(row[0] for row in cursor.fetchall())
            log(f"数据库中总 AppID 数量: {len(db_appids)}")
            
            log(f"output.json 中游戏类 AppID 数量: {len(game_appids)}")
            
            skipped_status = 0
            skipped_time = 0
            for appid_str in game_appids:
                appid_int = int(appid_str)
                if appid_int not in db_appids:
                    if appid_int not in recorded_appids:
                        invalid_appids.append({
                            "appid": appid_int,
                            "reason": "不在数据库或已下架",
                            "timestamp": datetime.now().isoformat()
                        })
                        log(f"AppID {appid_int} 不在数据库中，记录为无效", level="debug")
                    continue
                
                cursor.execute("SELECT scraper_status, last_checked FROM apps WHERE appid = ?", (appid_int,))
                row = cursor.fetchone()
                if row and row[0]:
                    skipped_status += 1
                    log(f"AppID {appid_int} 已处理 (scraper_status = true)", level="debug")
                    continue
                
                if row and row[1]:
                    try:
                        last_checked_time = datetime.fromisoformat(row[1])
                        if last_checked_time >= recheck_period:
                            skipped_time += 1
                            log(f"AppID {appid_int} 最近检查时间 {row[1]}，跳过", level="debug")
                            continue
                    except ValueError:
                        log(f"AppID {appid_int} 的 last_checked 格式错误: {row[1]}", level="debug")
                
                log(f"AppID {appid_int} 通过筛选，添加到待处理列表", level="debug")
                appids.append(appid_int)
            
            if invalid_appids:
                invalid_data["invalid_appids"] = invalid_data.get("invalid_appids", []) + invalid_appids
                with open(INVALID_LOG_PATH, 'w', encoding='utf-8') as f:
                    json.dump(invalid_data, f, indent=2, ensure_ascii=False)
                log(f"发现 {len(invalid_appids)} 个新无效 AppID，已记录至 {INVALID_LOG_PATH}")
            
            log(f"跳过 {skipped_status} 个 AppID (scraper_status = true)")
            log(f"跳过 {skipped_time} 个 AppID (last_checked 最近)")
            log(f"从 output.json 加载到 {len(appids)} 个待处理游戏类 AppID")
            return appids[:100]
    except Exception as e:
        log(f"加载 output.json 失败: {str(e)}")
        return []

def check_game(appid, rate_limiter):
    url = f"https://store.steampowered.com/api/appdetails?appids={appid}&l=schinese"
    max_attempts = 3
    for attempt in range(max_attempts):
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
                chinese_keywords = ['schinese', 'tchinese', '中文', '简体', '繁体', 'Chinese', 'Simplified Chinese', 'Traditional Chinese']
                has_chinese = any(kw in langs.lower() for kw in chinese_keywords)
                has_cards = any(cat.get("id") == 29 for cat in game_info.get("categories", []))
                log(f"游戏 {appid} => {'支持中文' if has_chinese else '无中文'} | {'有卡牌' if has_cards else '无卡牌'} | 响应时间: {duration:.2f}秒")
                return {
                    "appid": appid,
                    "name": game_info.get("name", f"Unknown_{appid}"),
                    "type": game_info.get("type", "game"),
                    "supports_chinese": has_chinese,
                    "supports_cards": has_cards,
                    "last_checked": datetime.utcnow().isoformat()
                }
            else:
                log(f"获取 AppID: {appid} 的详情失败 (尝试 {attempt + 1}/{max_attempts})", level="debug")
                log_failed_appid(appid, "API 返回 success: false")
                if attempt < max_attempts - 1:
                    time.sleep(5)
                continue
        except requests.exceptions.RequestException as e:
            if "429" in str(e):
                log(f"触发 429 错误，暂停 5 分钟后重试... (尝试 {attempt + 1}/{max_attempts})", level="debug")
                time.sleep(300)
                continue
            else:
                log(f"请求 AppID: {appid} 失败: {e} (尝试 {attempt + 1}/{max_attempts})", level="debug")
                log_failed_appid(appid, str(e))
                if attempt < max_attempts - 1:
                    time.sleep(5)
                continue
    return None

def save_data(data, file_path):
    try:
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        file_size = file_path.stat().st_size / (1024 * 1024)
        log(f"数据已保存至 {file_path}，文件大小: {file_size:.2f} MB")
        if file_size > 50:
            log(f"警告：{file_path} 文件大小超过 50MB，可能影响 GitHub 提交")
    except Exception as e:
        log(f"保存失败: {str(e)}")
        raise

def main():
    log("脚本启动")
    chinese_data = safe_load_json(DATA_DIR / "chinese_games.json")
    card_data = safe_load_json(DATA_DIR / "card_games.json")
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("PRAGMA table_info(apps)")
    columns = [column[1] for column in cursor.fetchall()]
    if 'retry_count' not in columns:
        log("检测到数据库缺少 retry_count 字段，正在更新表结构...")
        cursor.execute('ALTER TABLE apps ADD COLUMN retry_count INTEGER DEFAULT 0')
    if 'last_checked' not in columns:
        log("检测到数据库缺少 last_checked 字段，正在更新表结构...")
        cursor.execute('ALTER TABLE apps ADD COLUMN last_checked TEXT')
    
    # 总是尝试初始化 last_checked
    initialize_last_checked(conn, cursor, chinese_data, card_data)
    conn.commit()
    
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS apps (
        appid INTEGER PRIMARY KEY,
        status BOOLEAN DEFAULT FALSE,
        scraper_status BOOLEAN DEFAULT FALSE,
        retry_count INTEGER DEFAULT 0,
        last_checked TEXT
    )
    ''')
    conn.commit()
    
    output_path = DATA_DIR / "output.json"
    if output_path.exists():
        with open(output_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        game_appids = [int(appid_str) for appid_str, app_info in data.items() if app_info == "game"]
        if game_appids:
            both_chinese_and_cards = set(chinese_data["games"].keys()) & set(card_data["games"].keys())
            cursor.execute(
                f"SELECT appid FROM apps WHERE last_checked IS NOT NULL AND appid IN ({','.join(['?'] * len(game_appids))})",
                game_appids
            )
            processed_appids = set(row[0] for row in cursor.fetchall())
            
            reset_appids = [
                appid for appid in game_appids
                if appid not in processed_appids and str(appid) not in both_chinese_and_cards
            ]
            if reset_appids:
                cursor.execute(
                    f"UPDATE apps SET scraper_status = FALSE, retry_count = 0 WHERE appid IN ({','.join(['?'] * len(reset_appids))})",
                    reset_appids
                )
                conn.commit()
                reset_count = cursor.rowcount
                log(f"重置 {reset_count} 个游戏类 AppID 的 scraper_status 为 FALSE")
    
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
            scraper_status = True if result["supports_chinese"] and result["supports_cards"] else False
            cursor.execute(
                "UPDATE apps SET scraper_status = ?, retry_count = 0, last_checked = ? WHERE appid = ?",
                (scraper_status, datetime.utcnow().isoformat(), appid)
            )
        else:
            failure_count += 1
            cursor.execute("UPDATE apps SET retry_count = retry_count + 1 WHERE appid = ?", (appid,))
            cursor.execute("SELECT retry_count FROM apps WHERE appid = ?", (appid,))
            retry_count = cursor.fetchone()[0]
            if retry_count >= 5:
                cursor.execute(
                    "UPDATE apps SET scraper_status = TRUE, last_checked = ? WHERE appid = ?",
                    (datetime.utcnow().isoformat(), appid)
                )
                log(f"AppID {appid} 重试次数达到 5 次，标记为已处理")
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
    
    log(f"完成！累计中文游戏: {len(chinese_data['games'])}")
    log(f"完成！累计卡牌游戏: {len(card_data['games'])}")

    if os.getenv("GITHUB_ACTIONS") == "true":
        with open(os.getenv("GITHUB_OUTPUT"), 'a') as f:
            f.write(f"processed={len(test_appids)}\n")
            f.write(f"new_chinese={len(chinese_data['games'])}\n")
            f.write(f"new_cards={len(card_data['games'])}\n")

    cursor.close()
    conn.close()

if __name__ == "__main__":
    main()
