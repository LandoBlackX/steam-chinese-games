import os
import sqlite3
import time
import requests
from pathlib import Path

# 动态确定数据目录
BASE_DIR = Path(os.environ.get('GITHUB_WORKSPACE', Path(__file__).parent.parent))
DATA_DIR = BASE_DIR / 'data'
DATA_DIR.mkdir(exist_ok=True, parents=True)

db_path = DATA_DIR / 'app_list.db'

def is_valid_db(db_path):
    """检查 DB 是否有效 SQLite 文件"""
    if not db_path.exists():
        return False
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        conn.close()
        return True
    except sqlite3.Error as e:
        print(f"DB 检查失败: {e}")
        return False

getAppList_URL = "https://api.steampowered.com/ISteamApps/GetAppList/v2/"
response = requests.get(getAppList_URL)

if response.status_code == 200:
    data = response.json()
    app_list = data['applist']['apps']
    app_ids = [(app['appid'],) for app in app_list]

    # 检查并修复 DB
    if is_valid_db(db_path):
        print(f"数据库 {db_path} 有效，继续使用")
    else:
        print(f"数据库 {db_path} 无效或损坏，正在重建...")
        if db_path.exists():
            db_path.unlink()  # 删损坏文件
        print("重建新数据库")

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute('''
    CREATE TABLE IF NOT EXISTS
    apps (
        appid INTEGER PRIMARY KEY,
        status BOOLEAN DEFAULT FALSE,
        scraper_status BOOLEAN DEFAULT FALSE
    )
    ''')
    conn.commit()

    new_count = 0
    start_time = time.time()

    for app_id in app_ids:
        cursor.execute('''
            INSERT OR IGNORE INTO apps (appid) VALUES (?) ''', app_id)
        if cursor.rowcount > 0:
            new_count += 1
            if new_count <= 10:  # 限日志，只打印前 10 个
                print(f"新增 appid: {app_id[0]}")

    conn.commit()
    end_time = time.time()
    conn.close()

    total_time = end_time - start_time
    print(f"新增 {new_count} 个 appid ,已成功写入数据库。")
    print(f"总耗费时间: {total_time:.6f} 秒")
else:
    print(f"请求失败，状态码: {response.status_code}")
