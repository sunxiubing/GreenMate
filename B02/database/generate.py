"""B02 Step 3 — 主程序入口

功能：
  1. 初始化数据库 schema
  2. 检查是否有已有数据
  3. 如无数据，从模拟器生成并写入 SQLite
  4. 写完后验证数量、顺序、扰动分布
  5. 输出报告

用法：
  python generate.py           # 生成数据（如已有数据则跳过）
  python generate.py --force   # 强制重新生成（清空旧数据）
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

_self_path = Path(__file__).resolve().parent
_b02_root = _self_path.parent
if str(_b02_root) not in sys.path:
    sys.path.insert(0, str(_b02_root))

from SPEC import SAMPLE_COUNT, SAMPLE_DAYS, SAMPLE_INTERVAL_MINUTES
from simulator.engine import GreenhouseSimulator
from database.store import (
    DB_PATH,
    init_schema,
    is_empty,
    write_samples,
    row_count,
    verify_count,
    verify_order,
    all_samples,
    first_timestamp,
    last_timestamp,
)


def report() -> dict:
    """生成统计报告。"""
    samples = all_samples()
    if not samples:
        return {}

    temps = [s.temperature for s in samples]
    humidities = [s.humidity for s in samples]
    luxes = [s.lux for s in samples]

    scenario_counts: dict[str, int] = {}
    for s in samples:
        scenario_counts[s.sim_scenario] = scenario_counts.get(s.sim_scenario, 0) + 1

    half = SAMPLE_COUNT // 2
    first_half_scenarios = {s.sim_scenario for s in samples[:half]}
    second_half_scenarios = {s.sim_scenario for s in samples[half:]}

    return {
        "db_path": str(DB_PATH),
        "total_count": len(samples),
        "expected_count": SAMPLE_COUNT,
        "first_timestamp": samples[0].timestamp.isoformat(),
        "last_timestamp": samples[-1].timestamp.isoformat(),
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
        "scenarios": dict(sorted(scenario_counts.items())),
        "disturbance_scenarios": [k for k in scenario_counts if k != "normal"],
        "first_half_has_disturbance": bool(first_half_scenarios - {"normal"}),
        "second_half_has_disturbance": bool(second_half_scenarios - {"normal"}),
        "both_halves_have_disturbance": (
            bool(first_half_scenarios - {"normal"}) and
            bool(second_half_scenarios - {"normal"})
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="B02 数据生成与持久化")
    parser.add_argument("--force", action="store_true", help="强制重新生成，清空旧数据")
    args = parser.parse_args()

    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(f"[{datetime.now().strftime('%H:%M:%S')}] B02 数据生成器启动")
    print(f"  数据库: {DB_PATH}")
    print(f"  预期样本: {SAMPLE_COUNT} 条 ({SAMPLE_DAYS} 天 × {1440//SAMPLE_INTERVAL_MINUTES} 点/天)")

    init_schema()

    if not is_empty():
        if args.force:
            print("  --force: 清空旧数据，重新生成…")
            import sqlite3
            conn = sqlite3.connect(DB_PATH, isolation_level=None)
            conn.execute("DELETE FROM environment_samples;")
            conn.execute("VACUUM;")
            conn.close()
        else:
            count = row_count()
            ok, msg = verify_count()
            print(f"  数据库已有 {count} 条记录，跳过生成（用 --force 强制重刷）")
            print(f"  数量校验: {'✓' if ok else '✗'} {msg}")
            result = report()
            print(f"\n  统计报告:")
            print(f"    温度:   {result['temperature']['min']}°C ~ {result['temperature']['max']}°C  "
                  f"(均值 {result['temperature']['mean']}°C)")
            print(f"    湿度:   {result['humidity']['min']}% ~ {result['humidity']['max']}%  "
                  f"(均值 {result['humidity']['mean']}%)")
            print(f"    光照:   {result['lux']['min']} ~ {result['lux']['max']} lux  "
                  f"(均值 {result['lux']['mean']} lux)")
            print(f"    场景:   {result['scenarios']}")
            print(f"    前半段含扰动: {result['first_half_has_disturbance']}  "
                  f"后半段含扰动: {result['second_half_has_disturbance']}")
            return 0

    print("  生成模拟数据…")
    sim = GreenhouseSimulator(seed=42)
    samples = sim.generate()
    print(f"  生成完成: {len(samples)} 条")

    print("  写入数据库…")
    inserted = write_samples(samples)
    print(f"  写入完成: {inserted} 条")

    print("\n  校验:")
    ok, msg = verify_count()
    print(f"    数量: {'✓' if ok else '✗'} {msg}")
    ok, msg = verify_order()
    print(f"    顺序: {'✓' if ok else '✗'} {msg}")

    result = report()
    print(f"\n  统计报告:")
    print(f"    时间范围: {result['first_timestamp']} ~ {result['last_timestamp']}")
    print(f"    温度:   {result['temperature']['min']}°C ~ {result['temperature']['max']}°C  "
          f"(均值 {result['temperature']['mean']}°C)")
    print(f"    湿度:   {result['humidity']['min']}% ~ {result['humidity']['max']}%  "
          f"(均值 {result['humidity']['mean']}%)")
    print(f"    光照:   {result['lux']['min']} ~ {result['lux']['max']} lux  "
          f"(均值 {result['lux']['mean']} lux)")
    print(f"    场景分布: {result['scenarios']}")
    print(f"    扰动场景: {result['disturbance_scenarios']}")
    print(f"    前半段含扰动: {result['first_half_has_disturbance']}  "
          f"后半段含扰动: {result['second_half_has_disturbance']}")
    print(f"    前后半段均有扰动: {result['both_halves_have_disturbance']}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
