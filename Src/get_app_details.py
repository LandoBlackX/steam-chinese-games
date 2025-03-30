import requests
import sqlite3
import json
import time
import os
from pathlib import Path

BASE_DIR = Path(os.environ.get('GITHUB_WORKSPACE', Path(__file__).parent.parent))
DATA_DIR = BASE_DIR / 'data'
DATA_DIR.mkdir(exist_ok=True, parents=True)
db_path = DATA_DIR / "app_list.db"
output_file = DATA_DIR / "output.json"
failed_file = DATA_DIR / "failed_appids.json"
getDetails_URL = "https://store.steampowered.com/api/appdetails?l=english&appids="

def log(message):
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}", flush=True)

def log_failed_appid(appid, reason):
    failed_data = {}
    if failed_file.exists():
        with open(failed_file, 'r', encoding='utf-8') as f:
            failed_data = json.load(f)
    failed_data[str(appid)] = reason
    with open(failed_file, 'w', encoding='utf-8') as f:
        json.dump(failed_data, f, indent=2, ensure_ascii=False)

def load_existing_results():
    """加载分类本（output.json）中的已有结果"""
    if output_file.exists():
        with open(output_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

def check_app(appid):
    """查询单个 AppID 的类型"""
    url = f"{getDetails_URL}{appid}"
    try:
        response = requests.get(url, verify=False, timeout=15)
        response.raise_for_status()
        data = response.json()
        appid_str = str(appid)
        if data.get(appid_str, {}).get('success'):
            app_data = data[appid_str]['data']
            app_type = app_data.get('type', 'Unknown')
            log(f"AppID: {appid}, 类型: {app_type}")
            return app_type
        else:
            reason = f"API 返回: {data.get(appid_str, '无数据')}"
            log(f"获取 AppID: {appid} 的详情失败，{reason}")
            log_failed_appid(appid, reason)
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
    except ValueError as e:
        log(f"AppID: {appid} 的 JSON 解析失败: {e}")
        log_failed_appid(appid, f"JSON 解析失败: {e}")
        return None

def update_status(conn, cursor, appid, success=True):
    """更新数据库状态"""
    if success:
        cursor.execute("UPDATE apps SET status = true, retry_count = 0 WHERE appid = ?", (appid,))
    else:
        cursor.execute("UPDATE apps SET retry_count = retry_count + 1 WHERE appid = ?", (appid,))
    conn.commit()

def write_results_to_file(results):
    """将结果写入分类本（output.json）"""
    existing_results = load_existing_results()
    existing_results.update(results)
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(existing_results, f, ensure_ascii=False, indent=4)

def main():
    log("开始运行 get_app_details.py")

    # 连接数据库
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # 加载未处理的 AppID
    rows = cursor.execute("SELECT appid FROM apps WHERE status = false AND retry_count < 3").fetchall()
    appids = [row[0] for row in rows[:100]]  # 每次最多处理 100 个
    if not appids:
        log("没有需要处理的新 AppID，终止执行")
        cursor.close()
        conn.close()
        return

    log(f"从数据库加载 {len(appids)} 个待处理 AppID")

    # 加载已有结果（分类本）
    existing_results = load_existing_results()
    results = {}
    success_count = 0
    failure_count = 0
    to_query = []

    # 检查哪些 AppID 需要查询
    for appid in appids:
        appid_str = str(appid)
        if appid_str in existing_results:
            # 如果分类本已有记录，直接使用
            results[appid] = existing_results[appid_str]
            success_count += 1
            log(f"AppID: {appid} 已存在于 output.json，类型: {results[appid]}，跳过查询")
        else:
            # 需要查询的 AppID
            to_query.append(appid)

    # 查询剩余的 AppID
    log(f"需要查询的 AppID 数量: {len(to_query)}")
    for appid in to_query:
        app_type = check_app(appid)
        if app_type:
            results[appid] = app_type
            success_count += 1
        else:
            failure_count += 1
        update_status(conn, cursor, appid, success=(app_type is not None))

    # 保存结果
    if results:
        write_results_to_file(results)
        log(f"已更新 output.json，新增或更新 {len(results)} 个 AppID")

    log(f"处理完成！成功: {success_count}, 失败: {failure_count}")
    cursor.close()
    conn.close()

    log("完成 get_app_details.py，等待 30 秒后继续...")
    time.sleep(30)

if __name__ == "__main__":
    main()
