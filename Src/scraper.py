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
            
            # 获取已处理的 AppID
            cursor.execute("SELECT appid FROM apps WHERE scraper_status = 1")
            processed_appids = set(row[0] for row in cursor.fetchall())
            log(f"数据库状态：已处理 {len(processed_appids)} 个 AppID")
            
            for appid, app_info in data.items():
                if app_info == "game":
                    appid_int = int(appid)
                    if appid_int in processed_appids:
                        continue
                    existing_c = existing_chinese["games"].get(appid, {})
                    existing_card = existing_cards["games"].get(appid, {})
                    last_checked = existing_c.get("last_checked") or existing_card.get("last_checked")
                    if not last_checked or datetime.fromisoformat(last_checked) < thirty_days_ago:
                        appids.append(appid_int)
            
            appids.sort(reverse=True)
            log(f"从 output.json 加载到 {len(appids)} 个待处理游戏类 AppID")
            return appids[:199]  # 每次处理 199 个 AppID
    except Exception as e:
        log(f"加载 output.json 失败: {str(e)}")
        return []

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
            log(f"获取 AppID: {appid} 的详情失败")
            log_failed_appid(appid, "API 返回 success: false")
            return None
    except requests.exceptions.RequestException as e:
        if "429" in str(e):
            log(f"触发 429 错误，暂停 5 分钟后重试...")
            time.sleep(300)
            return None
        else:
            log(f"请求 AppID: {appid} 失败: {e}")
            log_failed_appid(appid, str(e))
            return None

def save_data(data, file_path):
    try:
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        log(f"数据已保存至 {file_path}")
    except Exception as e:
        log(f"保存失败: {str(e)}")
        raise

def main():
    log("脚本启动")
    
    # 初始化数据库连接
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # 确保表结构存在
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS apps (
            appid INTEGER PRIMARY KEY,
            status BOOLEAN DEFAULT 0,
            scraper_status BOOLEAN DEFAULT 0
        )
        ''')
        conn.commit()
        
        # 测试数据库写入
        test_appid = 9999999  # 测试用虚拟ID
        cursor.execute("INSERT OR IGNORE INTO apps (appid, scraper_status) VALUES (?, ?)", (test_appid, 1))
        conn.commit()
        cursor.execute("SELECT scraper_status FROM apps WHERE appid = ?", (test_appid,))
        if cursor.fetchone()[0] != 1:
            log("错误：数据库写入测试失败，请检查权限")
            return
        cursor.execute("DELETE FROM apps WHERE appid = ?", (test_appid,))
        conn.commit()
        
    except sqlite3.Error as e:
        log(f"数据库初始化失败: {str(e)}")
        return
    
    # 加载数据文件
    chinese_data = safe_load_json(DATA_DIR / "chinese_games.json")
    card_data = safe_load_json(DATA_DIR / "card_games.json")
    
    # 加载待处理的 AppID
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
        else:
            failure_count += 1
        
        # 更新数据库状态
        try:
            cursor.execute("INSERT OR IGNORE INTO apps (appid) VALUES (?)", (appid,))
            cursor.execute("UPDATE apps SET scraper_status = 1 WHERE appid = ?", (appid,))
            conn.commit()
            
            # 验证更新
            cursor.execute("SELECT scraper_status FROM apps WHERE appid = ?", (appid,))
            if cursor.fetchone()[0] == 1:
                log(f"AppID: {appid} 标记成功")
            else:
                log(f"警告：AppID: {appid} 标记验证失败")
                
        except sqlite3.Error as e:
            log(f"数据库错误(AppID:{appid}): {str(e)}")
            conn.rollback()

    # 保存结果
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
    
    # 输出数据库状态报告
    cursor.execute("SELECT COUNT(*) FROM apps WHERE scraper_status = 1")
    processed_total = cursor.fetchone()[0]
    log(f"数据库状态报告：已处理总数 = {processed_total}")
    
    log(f"处理完成！成功: {success_count}, 失败: {failure_count}")
    log(f"累计中文游戏: {len(chinese_data['games'])}, 累计卡牌游戏: {len(card_data['games'])}")

    if os.getenv("GITHUB_ACTIONS") == "true":
        with open(os.getenv("GITHUB_OUTPUT"), 'a') as f:
            f.write(f"processed={len(test_appids)}\n")
            f.write(f"new_chinese={len(chinese_data['games'])}\n")
            f.write(f"new_cards={len(card_data['games'])}\n")

    cursor.close()
    conn.close()

if __name__ == "__main__":
    main()
