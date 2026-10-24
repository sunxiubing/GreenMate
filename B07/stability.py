"""B07 Step 1 — 稳定历史窗口准备 + 透明稳定性判定规则

目的：
  为后续步骤准备一段"稳定"的历史数据，并让判定过程完全透明、可复述。

设计约束：
  - 判定只能使用窗口内的点，不许看窗外数据
  - 不修改 B02 原始数据
  - 不接 LLM、不加载模型

数据来源：
  B02 SQLite `environment_samples` 表，按 timestamp 升序，只取真实原始行。

用法：
  python B07/stability.py            # 全量扫描 + 选窗
  python B07/stability.py --json     # 输出 JSON
"""

import sys
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))
# B02 作为平级目录，store.py / simulator 用的是 B02 内绝对导入
sys.path.insert(0, str(_PROJECT_ROOT / "B02"))

# --------------------------------------------------------------------------
# 判定阈值（可调常量）
# --------------------------------------------------------------------------
TEMP_RANGE_MAX = 2.0        # 3小时内温度最大波动（℃）
HUMI_TOTAL_MAX = 3.0        # 湿度首尾总变化（%）
HUMI_STEP_MAX = 1.5         # 湿度单步最大变化（%）
LUX_JUMP_MAX = 15000        # 光照单步最大跳变（lux）
REQUIRE_NO_MONOTONE = True  # 湿度不得全程单调上升或全程单调下降

# 选窗策略参数
DAYTIME_LUX_MIN = 1000.0    # 窗口内 max lux 超过此值视为白天段
SAMPLE_INTERVAL_HOURS = 0.5  # 采样间隔（小时）


# --------------------------------------------------------------------------
# 数据读取
# --------------------------------------------------------------------------

def load_all_samples() -> list:
    """从 B02 SQLite 读取全部样本，按 timestamp 升序。

    只取真实原始行，不做任何加工或修改。
    """
    from simulator.engine import Sample
    from database.store import get_connection, init_schema

    init_schema()
    conn = get_connection()
    cur = conn.execute(
        """
        SELECT timestamp, temperature, humidity, lux, source_type, sim_scenario
        FROM environment_samples
        ORDER BY timestamp ASC, id ASC;
        """
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


# --------------------------------------------------------------------------
# 判定
# --------------------------------------------------------------------------

def _is_monotone(vals: list[float]) -> tuple[bool, str]:
    """判断序列是否全程单调上升或全程单调下降。

    Returns: (是否单调, 描述)
    """
    if len(vals) < 2:
        return False, "点数不足，无法判定趋势"

    diffs = [vals[i + 1] - vals[i] for i in range(len(vals) - 1)]

    if all(d > 0 for d in diffs):
        return True, "全程单调上升"
    if all(d < 0 for d in diffs):
        return True, "全程单调下降"
    return False, "无持续单向趋势"


def judge_stability(samples: list) -> dict:
    """对一组样本做稳定性判定。

    只使用传入样本本身，不访问任何窗外数据。

    Args:
        samples: 窗口内的样本列表（按时间升序）

    Returns:
        {
            "passed": bool,
            "criteria": [
                {"name", "value", "threshold", "passed", "note"},
                ...
            ],
            "conclusion": str,   # 可被人复述的中文结论，带具体数值
        }
    """
    if not samples:
        return {
            "passed": False,
            "criteria": [],
            "conclusion": "无数据，无法判定 → 判定为不稳定",
        }

    temps = [s.temperature for s in samples]
    hums = [s.humidity for s in samples]
    luxs = [s.lux for s in samples]

    # --- 判据 1：温度最大波动 ---
    temp_range = max(temps) - min(temps)
    c1_pass = temp_range <= TEMP_RANGE_MAX
    c1 = {
        "name": "温度最大波动",
        "value": round(temp_range, 2),
        "threshold": TEMP_RANGE_MAX,
        "passed": c1_pass,
        "note": f"{min(temps):.1f}~{max(temps):.1f}℃，波动 {temp_range:.1f}℃",
    }

    # --- 判据 2：湿度首尾总变化 ---
    humi_total = hums[-1] - hums[0]
    c2_pass = abs(humi_total) <= HUMI_TOTAL_MAX
    c2 = {
        "name": "湿度首尾总变化",
        "value": round(humi_total, 2),
        "threshold": HUMI_TOTAL_MAX,
        "passed": c2_pass,
        "note": f"首 {hums[0]:.1f}% → 尾 {hums[-1]:.1f}%，变化 {humi_total:+.1f}%",
    }

    # --- 判据 3：湿度单步最大变化 ---
    humi_steps = [abs(hums[i + 1] - hums[i]) for i in range(len(hums) - 1)]
    humi_step_max = max(humi_steps) if humi_steps else 0.0
    c3_pass = humi_step_max <= HUMI_STEP_MAX
    c3 = {
        "name": "湿度单步最大变化",
        "value": round(humi_step_max, 2),
        "threshold": HUMI_STEP_MAX,
        "passed": c3_pass,
        "note": f"单步最大 {humi_step_max:.1f}%",
    }

    # --- 判据 4：光照单步最大跳变 ---
    lux_steps = [abs(luxs[i + 1] - luxs[i]) for i in range(len(luxs) - 1)]
    lux_step_max = max(lux_steps) if lux_steps else 0.0
    c4_pass = lux_step_max <= LUX_JUMP_MAX
    c4 = {
        "name": "光照单步最大跳变",
        "value": round(lux_step_max, 1),
        "threshold": LUX_JUMP_MAX,
        "passed": c4_pass,
        "note": f"单步最大 {lux_step_max:.0f} lux",
    }

    # --- 判据 5：湿度无单调趋势 ---
    monotone, mono_desc = _is_monotone(hums)
    c5_pass = (not monotone) if REQUIRE_NO_MONOTONE else True
    c5 = {
        "name": "湿度无持续单向趋势",
        "value": mono_desc,
        "threshold": "禁止全程单调" if REQUIRE_NO_MONOTONE else "不检查",
        "passed": c5_pass,
        "note": mono_desc,
    }

    criteria = [c1, c2, c3, c4, c5]
    passed = all(c["passed"] for c in criteria)

    # --- 结论：一句可被人复述的话，带具体数值 ---
    hours = len(samples) * SAMPLE_INTERVAL_HOURS
    hours_txt = f"{hours:g}"
    verdict = "稳定" if passed else "不稳定"

    conclusion = (
        f"近 {hours_txt} 小时温度 {min(temps):.1f}~{max(temps):.1f}℃"
        f"（波动 {temp_range:.1f}℃，阈值 {TEMP_RANGE_MAX}℃）；"
        f"湿度首尾 {humi_total:+.1f}%（阈值 {HUMI_TOTAL_MAX}%）、"
        f"单步最大 {humi_step_max:.1f}%；"
        f"光照单步最大 {lux_step_max:.0f} lux（阈值 {LUX_JUMP_MAX}）；"
        f"{mono_desc} → 判定为{verdict}"
    )

    return {
        "passed": passed,
        "criteria": criteria,
        "conclusion": conclusion,
    }


# --------------------------------------------------------------------------
# 全量扫描
# --------------------------------------------------------------------------

def scan_stable_windows(all_samples: list, window_size: int = 6) -> list[dict]:
    """扫描全部连续 window_size 点窗口，列出所有通过判据的窗口。

    Returns:
        每项含：起止时间、关键数值、结论、是否白天段
    """
    results: list[dict] = []

    for start in range(0, len(all_samples) - window_size + 1):
        win = all_samples[start:start + window_size]
        judged = judge_stability(win)
        if not judged["passed"]:
            continue

        temps = [s.temperature for s in win]
        hums = [s.humidity for s in win]
        luxs = [s.lux for s in win]

        results.append({
            "window_index": start,
            "start_time": win[0].timestamp.isoformat(),
            "end_time": win[-1].timestamp.isoformat(),
            "is_daytime": max(luxs) > DAYTIME_LUX_MIN,
            "max_lux": round(max(luxs), 1),
            "temp_min": round(min(temps), 1),
            "temp_max": round(max(temps), 1),
            "temp_range": round(max(temps) - min(temps), 2),
            "humi_total": round(hums[-1] - hums[0], 2),
            "humi_step_max": round(max(abs(hums[i + 1] - hums[i]) for i in range(len(hums) - 1)), 2),
            "lux_step_max": round(max(abs(luxs[i + 1] - luxs[i]) for i in range(len(luxs) - 1)), 1),
            "conclusion": judged["conclusion"],
            "criteria": judged["criteria"],
        })

    return results


# --------------------------------------------------------------------------
# 选窗
# --------------------------------------------------------------------------

def pick_stable_window(all_samples: list, window_size: int = 6) -> dict:
    """从合格窗口中选出一个。

    选窗策略（显式参数化）：
      1. 优先白天段（窗口内 max lux > DAYTIME_LUX_MIN）
      2. 再取离数据末点最近的合格窗口

    Returns:
        {
            "selected": {窗口详情} | None,
            "reason": str,
            "candidate_count": int,
            "daytime_count": int,
            "pool": "daytime" | "all",
        }
    """
    stable = scan_stable_windows(all_samples, window_size=window_size)

    if not stable:
        return {
            "selected": None,
            "reason": "没有通过判据的稳定窗口",
            "candidate_count": 0,
            "daytime_count": 0,
            "pool": "all",
        }

    daytime = [w for w in stable if w["is_daytime"]]
    pool = daytime if daytime else stable
    pool_name = "daytime" if daytime else "all"

    # 离数据末点最近 = end_time 最大
    selected = max(pool, key=lambda w: w["end_time"])

    if daytime:
        reason = (
            f"共 {len(stable)} 个合格窗口，其中白天段（max lux > {DAYTIME_LUX_MIN:.0f}）"
            f"{len(daytime)} 个；优先在白天段中选取，取离数据末点最近者"
            f"（end_time {selected['end_time']}，数据末点 {all_samples[-1].timestamp.isoformat()}）"
        )
    else:
        reason = (
            f"共 {len(stable)} 个合格窗口，无白天段候选（max lux 均未超过 {DAYTIME_LUX_MIN:.0f}）；"
            f"退而取离数据末点最近者（end_time {selected['end_time']}，"
            f"数据末点 {all_samples[-1].timestamp.isoformat()}）"
        )

    return {
        "selected": selected,
        "reason": reason,
        "candidate_count": len(stable),
        "daytime_count": len(daytime),
        "pool": pool_name,
    }


# --------------------------------------------------------------------------
# 主程序
# --------------------------------------------------------------------------

def main():
    import argparse

    parser = argparse.ArgumentParser(description="B07 Step 1: 稳定窗口扫描与选窗")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出")
    parser.add_argument("--window-size", type=int, default=6, help="窗口点数")
    args = parser.parse_args()

    if args.json:
        import json
        samples = load_all_samples()
        stable = scan_stable_windows(samples, window_size=args.window_size)
        picked = pick_stable_window(samples, window_size=args.window_size)
        print(json.dumps({
            "total_samples": len(samples),
            "stable_count": len(stable),
            "daytime_count": sum(1 for w in stable if w["is_daytime"]),
            "picked": picked,
        }, ensure_ascii=False, indent=2))
        return

    print("=" * 70)
    print("B07 Step 1 — 稳定历史窗口准备 + 透明判定规则")
    print("=" * 70)

    print("\n【判定阈值】")
    print(f"  TEMP_RANGE_MAX       = {TEMP_RANGE_MAX}   （3小时温度最大波动 ℃）")
    print(f"  HUMI_TOTAL_MAX       = {HUMI_TOTAL_MAX}   （湿度首尾总变化 %）")
    print(f"  HUMI_STEP_MAX        = {HUMI_STEP_MAX}   （湿度单步最大变化 %）")
    print(f"  LUX_JUMP_MAX         = {LUX_JUMP_MAX}  （光照单步最大跳变 lux）")
    print(f"  REQUIRE_NO_MONOTONE  = {REQUIRE_NO_MONOTONE}  （湿度禁止全程单调）")
    print(f"  DAYTIME_LUX_MIN      = {DAYTIME_LUX_MIN}  （白天段判定阈值）")

    print("\n加载 B02 原始数据...")
    samples = load_all_samples()
    print(f"  样本数: {len(samples)}")
    print(f"  时间范围: {samples[0].timestamp.isoformat()} → {samples[-1].timestamp.isoformat()}")

    print(f"\n扫描全部连续 {args.window_size} 点窗口...")
    stable = scan_stable_windows(samples, window_size=args.window_size)
    daytime = [w for w in stable if w["is_daytime"]]
    total_windows = len(samples) - args.window_size + 1
    print(f"  扫描窗口总数: {total_windows}")
    print(f"  合格稳定窗口: {len(stable)} 个")
    print(f"  其中白天段（max lux > {DAYTIME_LUX_MIN:.0f}）: {len(daytime)} 个")

    if stable:
        print("\n" + "=" * 70)
        print("【合格稳定窗口清单】")
        print("=" * 70)
        print(f"  {'#':>3}  {'起止时间':<42} {'ΔT':>6} {'ΔH':>7} {'ΔH/step':>8} {'ΔL/step':>9}  白天")
        print(f"  {'-'*3}  {'-'*42} {'-'*6} {'-'*7} {'-'*8} {'-'*9}  ----")
        for i, w in enumerate(stable, 1):
            print(f"  {i:>3}  {w['start_time']} → {w['end_time']}  "
                  f"{w['temp_range']:>6.1f} {w['humi_total']:>+7.1f} "
                  f"{w['humi_step_max']:>8.1f} {w['lux_step_max']:>9.0f}  "
                  f"{'是' if w['is_daytime'] else '否'}")

    print("\n" + "=" * 70)
    print("【选窗】")
    print("=" * 70)
    picked = pick_stable_window(samples, window_size=args.window_size)
    print(f"  理由: {picked['reason']}")

    sel = picked["selected"]
    if sel:
        print(f"\n  选中窗口: {sel['start_time']} → {sel['end_time']}")
        print(f"\n  判据明细:")
        for c in sel["criteria"]:
            icon = "✅" if c["passed"] else "❌"
            print(f"    {icon} {c['name']}: 实测={c['value']}  阈值={c['threshold']}  — {c['note']}")
        print(f"\n  结论: {sel['conclusion']}")
    else:
        print("  未选中任何窗口")

    print("\n" + "=" * 70)
    print("B07 Step 1 完成 ✅")
    print("=" * 70)


if __name__ == "__main__":
    main()
