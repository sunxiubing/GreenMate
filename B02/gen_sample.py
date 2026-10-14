"""B02 — 生成 20 条时序样本并写入 greenmate.db（根目录）

流程：
  1. 导入 GreenhouseSimulator
  2. 实例化模拟器，调用 generate() 获取 20 条样本
  3. 每条样本增加 source_type = "simulator"
  4. 建表 env_data（id, timestamp, temperature, humidity, lux, sim_scenario, source_type）
  5. 写入根目录 greenmate.db
"""

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

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp    DATETIME NOT NULL,
    temperature  REAL    NOT NULL,
    humidity     REAL    NOT NULL,
    lux          REAL    NOT NULL,
    sim_scenario TEXT    NOT NULL DEFAULT 'normal',
    source_type  TEXT    NOT NULL DEFAULT 'simulator'
);
"""


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, isolation_level=None)
    conn.execute("PRAGMA journal_mode = WAL;")
    return conn


def init_table() -> None:
    conn = get_conn()
    conn.execute(SCHEMA)
    # 表已存在但缺少列，逐一添加
    for col_def in [
        "ALTER TABLE {TABLE} ADD COLUMN sim_scenario TEXT NOT NULL DEFAULT 'normal'",
        "ALTER TABLE {TABLE} ADD COLUMN source_type TEXT NOT NULL DEFAULT 'simulator'",
    ]:
        try:
            conn.execute(col_def.format(TABLE=TABLE))
        except sqlite3.OperationalError:
            pass
    conn.close()


def write_samples(samples: list) -> int:
    conn = get_conn()
    conn.execute("BEGIN TRANSACTION;")
    count = 0
    for s in samples:
        conn.execute(
            f"INSERT INTO {TABLE} (timestamp, temperature, humidity, lux, sim_scenario, source_type) "
            f"VALUES (?, ?, ?, ?, ?, ?);",
            (
                s.timestamp.isoformat(),
                s.temperature,
                s.humidity,
                s.lux,
                s.sim_scenario,
                "simulator",
            ),
        )
        count += 1
    conn.execute("COMMIT;")
    conn.close()
    return count


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(f"[{datetime.now().strftime('%H:%M:%S')}] gen_sample 启动")
    print(f"  数据库: {DB_PATH}")

    # 1. 建表
    init_table()
    print("  表 env_data 建表完成")

    # 2. 实例化模拟器，生成 20 条样本
    sim = GreenhouseSimulator()
    all_samples = sim.generate()
    samples = all_samples[:20]
    print(f"  生成完成: {len(samples)} 条")
    for s in samples:
        print(
            f"    {s.timestamp}  {s.temperature}°C  {s.humidity}%  "
            f"{s.lux:.0f} lux  [{s.sim_scenario}]  source={s.source_type}"
        )

    # 3. 记录最早 timestamp
    first_ts = samples[0].timestamp.isoformat()
    print(f"  最早 timestamp: {first_ts}")

    # 4. 写入数据库
    inserted = write_samples(samples)
    print(f"  写入完成: {inserted} 条")

    # 5. 持久化验证
    conn = get_conn()
    cur = conn.execute(f"SELECT COUNT(*) FROM {TABLE};")
    total = cur.fetchone()[0]

    # 校验写入的数据
    cur = conn.execute(
        f"SELECT id, timestamp, temperature, humidity, lux, sim_scenario, source_type "
        f"FROM {TABLE} ORDER BY id DESC LIMIT 20;"
    )
    rows = cur.fetchall()
    conn.close()

    print(f"\n  === 持久化验证 ===")
    print(f"  env_data 表共 {total} 条记录")
    print(f"  最近 20 条：")
    for r in rows:
        print(f"    id={r[0]}  {r[1]}  {r[2]}°C  {r[3]}%  {r[4]:.0f} lux  [{r[5]}]  {r[6]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
