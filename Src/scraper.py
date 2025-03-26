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
    url = f"https://store.steampowered.com/api/appdetails?appids={appid}&l=schinese"
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

def save_data(data, file_path):
    """安全保存数据"""
    try:
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        log(f"成功保存数据到 {file_path}")
        log(f"文件大小: {os.path.getsize(file_path)} 字节")
        log(f"包含游戏数: {len(data.get('games', {}))}")
    except Exception as e:
        log(f"保存失败: {str(e)}")
        raise

def main():
    log("脚本启动")
    chinese_data = safe_load_json(DATA_DIR / "chinese_games.json")
    card_data = safe_load_json(DATA_DIR / "card_games.json")
    
    # 测试已知支持中文的AppID + 近期游戏范围
    test_appids = [
        570,     # Dota 2 (支持中文)
        730,     # CS2 (支持中文)
        1245620, # 艾尔登法环
        578080,  # PUBG
        1172470, # Apex Legends
        1091500, # 赛博朋克2077
        292030,  # 巫师3
        814380,  # 只狼
        275850,  # 饥荒
        105600,  # 泰拉瑞亚
    ] + list(range(1000000, 1000010))  # 近期游戏
    
    for appid in test_appids:
        result = check_game(appid)
        if result:
            appid_str = str(appid)
            if result["supports_chinese"]:
                chinese_data["games"][appid_str] = result
            if result["supports_cards"]:
                card_data["games"][appid_str] = result
            
            # 实时保存每个游戏的结果
            save_data(chinese_data, DATA_DIR / "chinese_games.json")
            save_data(card_data, DATA_DIR / "card_games.json")
        
        time.sleep(1.5)  # 遵守API限制
    
    log("脚本完成")
    
    # GitHub Actions 输出
    if os.getenv("GITHUB_ACTIONS") == "true":
        github_output = os.getenv("GITHUB_OUTPUT")
        if github_output:
            try:
                with open(github_output, 'a') as f:
                    f.write(f"processed={len(test_appids)}\n")
                    f.write(f"new_chinese={len(chinese_data['games'])}\n")
                    f.write(f"new_cards={len(card_data['games'])}\n")
            except Exception as e:
                log(f"写入GITHUB_OUTPUT失败: {str(e)}")

if __name__ == "__main__":
    main()
