"""B02 Step 4 — 历史查询接口（B07 预留）

对外提供两个函数：
    get_current_environment()  → 最新一条样本
    get_environment_history(start_time, end_time) → 时间窗口内所有样本

数据库路径固定为 greenmate.db，调用 init_schema() 确保表存在。
"""

import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Literal

_self_path = Path(__file__).resolve().parent


def _parse_datetime(s: str) -> datetime:
    """解析时间字符串，兼容 'YYYY-MM-DDTHH:MM'（datetime-local 输入）
    和 'YYYY-MM-DDTHH:MM:SS'（ISO 格式）以及带时区后缀的字符串。"""
    s = s.strip()
    # 去掉时区后缀（+08:00 或 Z），fromisoformat 无法直接处理
    if s.endswith("+08:00"):
        s = s[:-6]
    elif s.endswith("Z"):
        s = s[:-1]
    return datetime.fromisoformat(s)
_b02_root = _self_path.parent
if str(_b02_root) not in sys.path:
    sys.path.insert(0, str(_b02_root))

from simulator.engine import Sample
from database.store import get_connection, init_schema


def _row_to_sample(row: tuple) -> Sample:
    return Sample(
        timestamp=datetime.fromisoformat(row[0]),
        temperature=row[1],
        humidity=row[2],
        lux=row[3],
        source_type=row[4],
        sim_scenario=row[5],
    )


def get_current_environment() -> Sample | None:
    """获取最新一条样本。如无数据返回 None。"""
    init_schema()
    conn = get_connection()
    cur = conn.execute(
        """
        SELECT timestamp, temperature, humidity, lux, source_type, sim_scenario
        FROM environment_samples
        ORDER BY id DESC
        LIMIT 1;
        """
    )
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    return _row_to_sample(row)


def get_environment_history(
    start_time: datetime | str,
    end_time: datetime | str,
) -> list[Sample]:
    """获取指定时间窗口内的所有样本，按时间顺序返回。

    参数：
        start_time: 窗口起始时间（含）
        end_time:   窗口结束时间（含）

    返回：
        Sample 列表，空列表表示该窗口内无数据。
    """
    init_schema()
    if isinstance(start_time, str):
        start_time = _parse_datetime(start_time)
    if isinstance(end_time, str):
        end_time = _parse_datetime(end_time)

    conn = get_connection()
    cur = conn.execute(
        """
        SELECT timestamp, temperature, humidity, lux, source_type, sim_scenario
        FROM environment_samples
        WHERE timestamp >= ? AND timestamp <= ?
        ORDER BY id ASC;
        """,
        (start_time.isoformat(), end_time.isoformat()),
    )
    rows = cur.fetchall()
    conn.close()
    return [_row_to_sample(r) for r in rows]


def query_stats(
    start_time: datetime | str,
    end_time: datetime | str,
) -> dict:
    """返回指定时间窗口的统计摘要。

    返回：
        {
            "count": int,
            "temperature": {"min", "max", "mean"},
            "humidity":    {"min", "max", "mean"},
            "lux":         {"min", "max", "mean"},
            "trend": "rising" | "falling" | "stable" | "mixed",
            "scenario_counts": dict[str, int],
        }
    """
    samples = get_environment_history(start_time, end_time)
    if not samples:
        return {"count": 0, "error": "该时间窗口内无数据"}

    temps = [s.temperature for s in samples]
    humidities = [s.humidity for s in samples]
    luxes = [s.lux for s in samples]

    first_third = samples[:len(samples) // 3]
    last_third = samples[-len(samples) // 3:]
    avg_temp_first = sum(s.temperature for s in first_third) / len(first_third)
    avg_temp_last = sum(s.temperature for s in last_third) / len(last_third)
    temp_diff = avg_temp_last - avg_temp_first

    if temp_diff > 2:
        trend = "rising"
    elif temp_diff < -2:
        trend = "falling"
    elif len(samples) > 2:
        trend = "stable"
    else:
        trend = "mixed"

    scenario_counts: dict[str, int] = {}
    for s in samples:
        scenario_counts[s.sim_scenario] = scenario_counts.get(s.sim_scenario, 0) + 1

    return {
        "count": len(samples),
        "first_time": samples[0].timestamp.isoformat(),
        "last_time": samples[-1].timestamp.isoformat(),
        "temperature": {
            "min": round(min(temps), 1),
            "max": round(max(temps), 1),
            "mean": round(sum(temps) / len(temps), 2),
        },
        "humidity": {
            "min": round(min(humidities), 1),
            "max": round(max(humidities), 1),
            "mean": round(sum(humidities) / len(humidities), 2),
        },
        "lux": {
            "min": round(min(luxes), 0),
            "max": round(max(luxes), 0),
            "mean": round(sum(luxes) / len(luxes), 2),
        },
        "trend": trend,
        "scenario_counts": scenario_counts,
    }


if __name__ == "__main__":
    import json
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    current = get_current_environment()
    print(f"当前环境: {current.timestamp.isoformat()}  "
          f"{current.temperature}°C {current.humidity}% {current.lux} lux  "
          f"[{current.sim_scenario}]")

    # 最近 3 天统计
    from datetime import timedelta
    end = datetime.fromisoformat(current.timestamp.isoformat())
    start = end - timedelta(days=3)
    stats = query_stats(start, end)
    print(f"\n最近 3 天统计 ({stats['first_time']} ~ {stats['last_time']}):")
    print(json.dumps(stats, indent=2, ensure_ascii=False))
