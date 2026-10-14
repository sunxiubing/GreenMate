"""B02 — 读取 env_data 表数据，用于持久化验证

用法：
    python query_sample.py              # 查看全部记录
    python query_sample.py --last N    # 查看最近 N 条
    python query_sample.py --clear     # 清空 env_data 表
"""

import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

_self_path = Path(__file__).resolve().parent
_b02_root = _self_path.parent
if str(_b02_root) not in sys.path:
    sys.path.insert(0, str(_b02_root))

from simulator.engine import GreenhouseSimulator

DB_PATH = _self_path / "greenmate.db"
TABLE = "env_data"


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, isolation_level=None)
    conn.execute("PRAGMA journal_mode = WAL;")
    return conn


def query_all(limit: int | None = None) -> list[tuple]:
    conn = get_conn()
    if limit:
        cur = conn.execute(f"SELECT * FROM {TABLE} ORDER BY id DESC LIMIT ?;", (limit,))
    else:
        cur = conn.execute(f"SELECT * FROM {TABLE} ORDER BY id ASC;")
    rows = cur.fetchall()
    conn.close()
    return rows


def query_count() -> int:
    conn = get_conn()
    cur = conn.execute(f"SELECT COUNT(*) FROM {TABLE};")
    count = cur.fetchone()[0]
    conn.close()
    return count


def clear_table() -> None:
    conn = get_conn()
    conn.execute(f"DELETE FROM {TABLE};")
    conn.close()
    print(f"  已清空 {TABLE} 表")


def print_rows(rows: list[tuple]) -> None:
    COLS = ["id", "timestamp", "temperature", "humidity", "lux", "sim_scenario", "source_type"]
    print(f"\n{'  '.join(COLS)}")
    print("-" * 90)
    for row in rows:
        print("  ".join(str(v) for v in row))


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="查询 env_data 表")
    parser.add_argument("--last", type=int, help="查看最近 N 条")
    parser.add_argument("--clear", action="store_true", help="清空 env_data 表")
    args = parser.parse_args()

    if args.clear:
        clear_table()
        return 0

    total = query_count()
    print(f"[{datetime.now().strftime('%H:%M:%S')}] env_data 持久化验证")
    print(f"  数据库: {DB_PATH}")
    print(f"  总记录数: {total}")

    if total == 0:
        print("  表为空，请先运行 gen_sample.py 生成数据")
        return 0

    if args.last:
        rows = query_all(limit=args.last)
        print(f"  最近 {len(rows)} 条：")
    else:
        rows = query_all()
        print(f"  全部 {len(rows)} 条：")

    print_rows(rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
