import os
import sys
import requests
import json
from pathlib import Path
from datetime import datetime, timedelta
import time
from concurrent.futures import ThreadPoolExecutor

# 动态确定数据目录
BASE_DIR = Path(os.environ.get('GITHUB_WORKSPACE', Path(__file__).parent.parent))
DATA_DIR = BASE_DIR / 'data'
DATA_DIR.mkdir(exist_ok=True, parents=True)

def log(message):
    """统一日志格式"""
    print(f"[{datetime.now().isoformat()}] {message}", file=sys.stderr, flush=True)

def init_data_structure():
    """初始化数据结构"""
    return {
        "_metadata": {
            "created": datetime.utcnow().isoformat(),
            "updated": None,
            "version": 1
        },
        "games": {}
    }

def safe_load_json(file):
    """安全加载JSON文件"""
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

def load_game_appids(existing_chinese, existing_cards):
    """从output.json加载需要处理的游戏类AppID"""
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
            for appid, app_info in data.items():
                if app_info == "game":  # 注意：这里根据实际output.json格式调整
                    appid_int = int(appid)
                    existing_c = existing_chinese["games"].get(appid, {})
                    existing_card = existing_cards["games"].get(appid, {})
                    last_checked = existing_c.get("last_checked") or existing_card.get("last_checked")
                    if not last_checked or datetime.fromisoformat(last_checked) < thirty_days_ago:
                        appids.append(appid_int)
            log(f"从output.json加载到 {len(appids)} 个待处理游戏类AppID")
            return appids[:100]  # 每次最多处理100个
    except Exception as e:
        log(f"加载 output.json 失败: {str(e)}")
        return []

def check_game(appid):
    """检查单个游戏信息（含错误重试和2秒间隔）"""
    url = f"https://store.steampowered.com/api/appdetails?appids={appid}&l=schinese"
    retries = 3
    for attempt in range(retries):
        try:
            response = requests.get(url, timeout=15)
            response.raise_for_status()
            data = response.json().get(str(appid), {})
            if data.get("success", False):
                game_data = data["data"]
                langs = game_data.get("supported_languages", "") + "|" + game_data.get("languages", "")
                chinese_keywords = ['schinese', 'tchinese', '中文', '简体', '繁体']
                has_chinese = any(kw in langs.lower() for kw in chinese_keywords)
                has_cards = any(cat.get("id") == 29 for cat in game_data.get("categories", []))
                log(f"游戏 {appid} => {'支持中文' if has_chinese else '无中文'} | {'有卡牌' if has_cards else '无卡牌'}")
                return {
                    "appid": appid,
                    "name": game_data.get("name", f"Unknown_{appid}"),
                    "type": game_data.get("type", "game"),
                    "supports_chinese": has_chinese,
                    "supports_cards": has_cards,
                    "last_checked": datetime.utcnow().isoformat()
                }
        except requests.exceptions.RequestException as e:
            if attempt < retries - 1:
                wait = 2 ** attempt
                log(f"请求 {appid} 失败，{wait}秒后重试...")
                time.sleep(wait)
            else:
                log(f"检查游戏 {appid} 最终失败: {str(e)}")
    time.sleep(2)  # 每次请求后等待2秒
    return None

def save_data(data, file_path):
    """安全保存数据"""
    try:
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        log(f"数据已保存至 {file_path}")
    except Exception as e:
        log(f"保存失败: {str(e)}")
        raise

def main():
    log("脚本启动")
    
    # 初始化数据文件
    chinese_data = safe_load_json(DATA_DIR / "chinese_games.json")
    card_data = safe_load_json(DATA_DIR / "card_games.json")
    
    # 加载待处理游戏类AppID
    test_appids = load_game_appids(chinese_data, card_data)
    if not test_appids:
        log("没有需要处理的新AppID，终止执行")
        return
    
    log(f"开始处理 {len(test_appids)} 个AppID")

    # 单线程处理，确保每2秒一个请求
    results = []
    for appid in test_appids:
        result = check_game(appid)
        if result:
            results.append(result)

    # 更新数据
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

    # 统一保存
    if updated:
        timestamp = datetime.utcnow().isoformat()
        chinese_data["_metadata"]["updated"] = timestamp
        card_data["_metadata"]["updated"] = timestamp
        
        save_data(chinese_data, DATA_DIR / "chinese_games.json")
        save_data(card_data, DATA_DIR / "card_games.json")
    
    # 输出统计
    log(f"完成！累计中文游戏: {len(chinese_data['games'])}")
    log(f"完成！累计卡牌游戏: {len(card_data['games'])}")

    # GitHub Actions输出
    if os.getenv("GITHUB_ACTIONS") == "true":
        with open(os.getenv("GITHUB_OUTPUT"), 'a') as f:
            f.write(f"processed={len(test_appids)}\n")
            f.write(f"new_chinese={len(chinese_data['games'])}\n")
            f.write(f"new_cards={len(card_data['games'])}\n")

if __name__ == "__main__":
    main()
