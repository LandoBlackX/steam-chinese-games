import requests
import json
from pathlib import Path
from datetime import datetime
import time

DATA_DIR = Path(__file__).parent.parent / "data"

def load_or_create(file):
    """加载或初始化数据文件，确保结构完整"""
    if file.exists():
        with open(file, 'r') as f:
            data = json.load(f)
            # 修复点：确保数据结构完整
            if not isinstance(data, dict):
                data = {}
            if "_metadata" not in data:
                data["_metadata"] = {"created": datetime.utcnow().isoformat()}
            if "games" not in data:
                data["games"] = {}
            return data
    return {"_metadata": {"created": datetime.utcnow().isoformat()}, "games": {}}

def save_data(data, file):
    """保存数据并确保目录存在"""
    if not isinstance(data, dict):
        data = {"games": {}}
    if "_metadata" not in data:
        data["_metadata"] = {}
    
    data["_metadata"]["updated"] = datetime.utcnow().isoformat()
    file.parent.mkdir(exist_ok=True, parents=True)
    
    with open(file, 'w') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def check_game(appid):
    """检查单个游戏信息"""
    url = f"https://store.steampowered.com/api/appdetails?appids={appid}"
    try:
        res = requests.get(url, timeout=10)
        if res.status_code == 200:
            data = res.json().get(str(appid), {})
            if data.get("success"):
                game_data = data["data"]
                return {
                    "name": game_data.get("name"),
                    "supports_chinese": 'schinese' in game_data.get("supported_languages", "").lower(),
                    "supports_cards": any(cat["id"] == 29 for cat in game_data.get("categories", [])),
                    "last_checked": datetime.utcnow().isoformat()
                }
    except Exception as e:
        print(f"Error checking {appid}: {e}")
    return None

def main():
    # 初始化数据文件
    chinese_data = load_or_create(DATA_DIR / "chinese_games.json")
    card_data = load_or_create(DATA_DIR / "card_games.json")
    
    # 示例：检查前50个AppID（实际应使用GetAppList接口）
    for appid in range(1, 51):
        result = check_game(appid)
        if result:
            if result["supports_chinese"]:
                chinese_data["games"][str(appid)] = result
            if result["supports_cards"]:
                card_data["games"][str(appid)] = result
            
            # 每处理10个保存一次
            if appid % 10 == 0:
                save_data(chinese_data, DATA_DIR / "chinese_games.json")
                save_data(card_data, DATA_DIR / "card_games.json")
        
        time.sleep(1.5)  # 遵守API限制

if __name__ == "__main__":
    main()
