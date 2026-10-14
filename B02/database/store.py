"""B02 Step 3 — SQLite 持久化

数据库文件：greenmate.db
表：environment_samples

启动时自动建表（幂等）。
生成器产生样本后批量写入，执行 commit 落盘。
重启后校验数据完整性和时间顺序。

B07 接口调用 get_connection() 获取连接。
"""

import sqlite3
import sys
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

# 跨包导入，B02 作为平级目录处理
_self_path = Path(__file__).resolve().parent
_b02_root = _self_path.parent
if str(_b02_root) not in sys.path:
    sys.path.insert(0, str(_b02_root))

from SPEC import SAMPLE_COUNT, SAMPLE_INTERVAL_MINUTES
from simulator.engine import Sample


DB_PATH = _b02_root / "greenmate.db"
SCHEMA = """
CREATE TABLE IF NOT EXISTS environment_samples (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp     DATETIME NOT NULL,
    temperature   REAL    NOT NULL,
    humidity      REAL    NOT NULL,
    lux           REAL    NOT NULL,
    source_type   TEXT    NOT NULL DEFAULT 'simulated',
    sim_scenario  TEXT    NOT NULL DEFAULT 'normal'
);
"""
# 时间唯一索引：保证插入顺序和物理顺序一致
INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_samples_timestamp
    ON environment_samples (timestamp ASC);
"""


def get_connection() -> sqlite3.Connection:
    """返回数据库连接，启用外键约束和 WAL 模式。"""
    conn = sqlite3.connect(DB_PATH, isolation_level=None)
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    return conn


def init_schema() -> None:
    """启动时自动建表，幂等操作。"""
    conn = get_connection()
    conn.executescript(SCHEMA)
    conn.executescript(INDEX_SQL)
    conn.close()


def write_samples(samples: list[Sample]) -> int:
    """批量写入样本，执行 commit 后落盘。返回写入条数。"""
    conn = get_connection()
    conn.execute("BEGIN TRANSACTION;")
    inserted = 0
    for s in samples:
        conn.execute(
            """
            INSERT INTO environment_samples
                (timestamp, temperature, humidity, lux, source_type, sim_scenario)
            VALUES (?, ?, ?, ?, ?, ?);
            """,
            (s.timestamp.isoformat(), s.temperature, s.humidity, s.lux,
             s.source_type, s.sim_scenario),
        )
        inserted += 1
    conn.execute("COMMIT;")
    conn.close()
    return inserted


def row_count() -> int:
    """返回当前数据库中样本总数。"""
    conn = get_connection()
    cur = conn.execute("SELECT COUNT(*) FROM environment_samples;")
    count = cur.fetchone()[0]
    conn.close()
    return count


def is_empty() -> bool:
    return row_count() == 0


def verify_order() -> tuple[bool, str]:
    """校验 timestamp 是否严格递增，返回 (是否正确, 错误信息)。"""
    conn = get_connection()
    cur = conn.execute("""
        SELECT id, timestamp,
               LAG(timestamp) OVER (ORDER BY id ASC) AS prev_ts
        FROM environment_samples
        ORDER BY id ASC;
    """)
    errors = []
    for row in cur.fetchall():
        row_id, ts, prev_ts = row[0], row[1], row[2]
        if prev_ts is not None and ts <= prev_ts:
            errors.append(f"  id={row_id}: {ts} 不大于前一条 {prev_ts}")
    conn.close()
    if errors:
        return False, "\n".join(errors[:10])  # 只报告前 10 条
    return True, "时间顺序正确"


def verify_count() -> tuple[bool, str]:
    """校验样本总数是否为预期值 336。"""
    n = row_count()
    if n == SAMPLE_COUNT:
        return True, f"{n} 条（符合预期）"
    return False, f"{n} 条（预期 {SAMPLE_COUNT} 条）"


def all_samples() -> list[Sample]:
    """读取全部样本，按 id 顺序返回。"""
    conn = get_connection()
    cur = conn.execute(
        "SELECT timestamp, temperature, humidity, lux, source_type, sim_scenario "
        "FROM environment_samples ORDER BY id ASC;"
    )
    rows = cur.fetchall()
    conn.close()
    return [
        Sample(
            timestamp=datetime.fromisoformat(r[0]),
            temperature=r[1],
            humidity=r[2],
            lux=r[3],
            source_type=r[4],
            sim_scenario=r[5],
        )
        for r in rows
    ]


def first_timestamp() -> datetime | None:
    conn = get_connection()
    cur = conn.execute("SELECT MIN(timestamp) FROM environment_samples;")
    row = cur.fetchone()
    conn.close()
    if row and row[0]:
        return datetime.fromisoformat(row[0])
    return None


def last_timestamp() -> datetime | None:
    conn = get_connection()
    cur = conn.execute("SELECT MAX(timestamp) FROM environment_samples;")
    row = cur.fetchone()
    conn.close()
    if row and row[0]:
        return datetime.fromisoformat(row[0])
    return None


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(f"数据库路径: {DB_PATH}")
    init_schema()
    print(f"当前样本数: {row_count()}")
    print(f"首条时间:  {first_timestamp()}")
    print(f"末条时间:  {last_timestamp()}")
    ok, msg = verify_count()
    print(f"数量校验:  {'✓' if ok else '✗'} {msg}")
    ok, msg = verify_order()
    print(f"顺序校验:  {'✓' if ok else '✗'} {msg}")
