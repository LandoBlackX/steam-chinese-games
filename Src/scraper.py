import os
import sys
import requests
import json
from pathlib import Path
from datetime import datetime
import time

# 动态确定数据目录
if 'GITHUB_WORKSPACE' in os.environ:
    BASE_DIR = Path(os.environ['GITHUB_WORKSPACE'])
else:
    BASE_DIR = Path(__file__).parent.parent

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
                if not isinstance(data, dict):
                    return init_data_structure()
                if "_metadata" not in data:
                    data["_metadata"] = {}
                if "games" not in data:
                    data["games"] = {}
                return data
    except Exception as e:
        log(f"加载 {file} 失败: {str(e)}")
    return init_data_structure()

def check_game(appid):
    """检查单个游戏信息"""
    url = f"https://store.steampowered.com/api/appdetails?appids={appid}"
    try:
        log(f"正在检查游戏 {appid}")
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
        log(f"检查游戏 {appid} 时出错: {str(e)}")
    return None

def main():
    log("脚本启动")
    chinese_data = safe_load_json(DATA_DIR / "chinese_games.json")
    card_data = safe_load_json(DATA_DIR / "card_games.json")
    
    # 示例：检查前50个AppID
    for appid in range(1, 51):
        result = check_game(appid)
        if result:
            appid_str = str(appid)
            if result["supports_chinese"]:
                chinese_data["games"][appid_str] = result
            if result["supports_cards"]:
                card_data["games"][appid_str] = result
            
            # 每处理5个保存一次
            if appid % 5 == 0:
                with open(DATA_DIR / "chinese_games.json", 'w', encoding='utf-8') as f:
                    json.dump(chinese_data, f, indent=2, ensure_ascii=False)
                with open(DATA_DIR / "card_games.json", 'w', encoding='utf-8') as f:
                    json.dump(card_data, f, indent=2, ensure_ascii=False)
        
        time.sleep(1.5)  # 遵守API限制
    
    # 最终保存
    with open(DATA_DIR / "chinese_games.json", 'w', encoding='utf-8') as f:
        json.dump(chinese_data, f, indent=2, ensure_ascii=False)
    with open(DATA_DIR / "card_games.json", 'w', encoding='utf-8') as f:
        json.dump(card_data, f, indent=2, ensure_ascii=False)
    
    log("脚本完成")
    
    # GitHub Actions 输出
    if os.getenv("GITHUB_ACTIONS") == "true":
        print(f"::set-output name=processed::50")
        print(f"::set-output name=new_chinese::{len(chinese_data['games'])}")
        print(f"::set-output name=new_cards::{len(card_data['games'])}")

if __name__ == "__main__":
    main()
