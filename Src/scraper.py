import requests
import json
from pathlib import Path
from datetime import datetime
import time

DATA_DIR = Path(__file__).parent.parent / "data"
DATA_DIR.mkdir(exist_ok=True, parents=True)  # 确保目录存在

def init_data_structure():
    """返回一个保证有完整结构的数据字典"""
    return {
        "_metadata": {
            "created": datetime.utcnow().isoformat(),
            "updated": None,
            "version": 1
        },
        "games": {}
    }

def safe_load_json(file):
    """安全加载JSON文件，确保返回有效数据"""
    try:
        if file.exists():
            with open(file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                # 验证数据结构
                if not isinstance(data, dict):
                    return init_data_structure()
                if "_metadata" not in data:
                    data["_metadata"] = {}
                if "games" not in data:
                    data["games"] = {}
                return data
    except (json.JSONDecodeError, IOError) as e:
        print(f"Warning: Failed to load {file}, creating new. Error: {str(e)}")
    return init_data_structure()

def save_data(data, file):
    """安全保存数据"""
    if not isinstance(data, dict):
        data = init_data_structure()
    
    # 确保元数据存在
    if "_metadata" not in data:
        data["_metadata"] = {}
    data["_metadata"].update({
        "updated": datetime.utcnow().isoformat(),
        "version": data["_metadata"].get("version", 0) + 1
    })
    
    try:
        with open(file, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except IOError as e:
        print(f"Error saving {file}: {str(e)}")

def check_game(appid):
    """检查游戏信息"""
    url = f"https://store.steampowered.com/api/appdetails?appids={appid}"
    try:
        response = requests.get(url, timeout=15)
        response.raise_for_status()
        data = response.json().get(str(appid), {})
        
        if data.get("success", False):
            game_data = data["data"]
            return {
                "name": game_data.get("name", f"Unknown_{appid}"),
                "type": game_data.get("type", "unknown"),
                "supports_chinese": 'schinese' in game_data.get("supported_languages", "").lower(),
                "supports_cards": any(cat.get("id") == 29 for cat in game_data.get("categories", [])),
                "last_checked": datetime.utcnow().isoformat()
            }
    except Exception as e:
        print(f"Error checking app {appid}: {str(e)}")
    return None

def main():
    # 初始化数据
    chinese_data = safe_load_json(DATA_DIR / "chinese_games.json")
    card_data = safe_load_json(DATA_DIR / "card_games.json")
    
    # 示例：检查前50个AppID（实际应使用GetAppList接口）
    for appid in range(1, 51):
        result = check_game(appid)
        if result:
            appid_str = str(appid)
            # 更新中文游戏数据
            if result["supports_chinese"]:
                chinese_data["games"][appid_str] = result
            # 更新卡牌游戏数据
            if result["supports_cards"]:
                card_data["games"][appid_str] = result
            
            # 每处理5个保存一次（频繁保存防止中断丢失数据）
            if appid % 5 == 0:
                save_data(chinese_data, DATA_DIR / "chinese_games.json")
                save_data(card_data, DATA_DIR / "card_games.json")
        
        time.sleep(1.5)  # 遵守API限制
    
    # 最终保存
    save_data(chinese_data, DATA_DIR / "chinese_games.json")
    save_data(card_data, DATA_DIR / "card_games.json")

if __name__ == "__main__":
    main()
if __name__ == "__main__":
    # ...原有代码...
    
    # 添加GitHub Actions输出
    if os.getenv("GITHUB_ACTIONS") == "true":
        print(f"::set-output name=processed::{len(appids)}")
        print(f"::set-output name=new_chinese::{len(chinese_data['games'])}")
        print(f"::set-output name=new_cards::{len(card_data['games'])}")
