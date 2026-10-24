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
from typing import NamedTuple

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


def judge_stability(samples: list, rich_failure: bool = False) -> dict:
    """对一组样本做稳定性判定。

    只使用传入样本本身，不访问任何窗外数据。

    Args:
        samples: 窗口内的样本列表（按时间升序）
        rich_failure: 判据未通过时，是否用"超了哪个阈值、超了多少"的
            富化结论句（见 _rich_failure_conclusion）。
            默认 False —— S1 的结论文本逐字保持原样，不受本参数影响。

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

    if (not passed) and rich_failure:
        # 富化失败结论：明确写"不稳定"、写清超了哪个阈值、超了多少。
        conclusion = _rich_failure_conclusion(temps, hums, criteria, hours_txt)
        return {
            "passed": passed,
            "criteria": criteria,
            "conclusion": conclusion,
        }

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
# 富化失败结论（只用于判据未通过时；S1 默认输出不受影响）
# --------------------------------------------------------------------------

def _excess_of(c: dict) -> float | None:
    """失败判据超出阈值的幅度；只有数值型判据才有。"""
    th = c.get("threshold")
    val = c.get("value")
    if not isinstance(th, (int, float)) or isinstance(th, bool):
        return None
    if not isinstance(val, (int, float)) or isinstance(val, bool):
        return None
    if c["name"] == "湿度首尾总变化":
        return round(abs(val) - th, 2)
    return round(val - th, 2)


def _attach_excess(criteria: list) -> None:
    """给未通过的数值型判据补一个 excess 字段（超出阈值的幅度）。

    这个数字同时进入结论句和判据表，LLM 复述它时不会被判成"事实越界"。
    """
    for c in criteria:
        if c["passed"]:
            continue
        ex = _excess_of(c)
        if ex is not None:
            c["excess"] = ex


def _failure_text(c: dict) -> str:
    """单条未通过判据的文字说明。"""
    name = c["name"]
    v = c.get("value")
    th = c.get("threshold")
    ex = c.get("excess")

    if name == "温度最大波动":
        base = f"温度波动 {v:.1f}℃ 超过阈值 {th:.1f}℃"
    elif name == "湿度首尾总变化":
        base = f"湿度首尾变化 {v:+.1f}% 超过阈值 {th:.1f}%"
    elif name == "湿度单步最大变化":
        base = f"湿度单步最大变化 {v:.1f}% 超过阈值 {th:.1f}%"
    elif name == "光照单步最大跳变":
        base = f"光照单步最大跳变 {v:.0f} lux 超过阈值 {th:.0f} lux"
    elif name == "湿度无持续单向趋势":
        return f"湿度{c.get('note')}（判据禁止全程单调）"
    else:
        return f"{name}：实测 {v}，阈值 {th}"

    if ex is not None:
        base += f"（超出 {ex:.1f}）" if name != "光照单步最大跳变" else f"（超出 {ex:.0f} lux）"
    return base


def _failure_reason(criteria: list, temps: list) -> str:
    """给"不稳定"配一个数据驱动的原因短语（不写死任何结论）。"""
    failed = {c["name"] for c in criteria if not c["passed"]}
    diffs = [temps[i + 1] - temps[i] for i in range(len(temps) - 1)]
    rising = bool(diffs) and all(d > 0 for d in diffs)
    falling = bool(diffs) and all(d < 0 for d in diffs)

    if "温度最大波动" in failed and rising:
        return "持续升温"
    if "温度最大波动" in failed and falling:
        return "持续降温"
    if "湿度无持续单向趋势" in failed:
        return "湿度持续单向变化"
    if "湿度首尾总变化" in failed or "湿度单步最大变化" in failed:
        return "湿度剧变"
    if "光照单步最大跳变" in failed:
        return "光照跳变"
    return "多指标波动"


def _rich_failure_conclusion(temps: list, hums: list, criteria: list,
                             hours_txt: str) -> str:
    """判据未通过时的富化结论句。

    形如：
      近 3 小时温度 25.3→32.8℃ 单调上升，温度波动 7.5℃ 超过阈值 2.0℃（超出 5.5）；
      湿度首尾变化 -12.1% 超过阈值 3.0%（超出 9.1）；… → 判定为不稳定（持续升温）

    只用窗口内的三列数值推导，不看 sim_scenario，也没有任何预设结论。
    """
    _attach_excess(criteria)

    t_first, t_last = temps[0], temps[-1]
    diffs = [temps[i + 1] - temps[i] for i in range(len(temps) - 1)]
    if diffs and all(d > 0 for d in diffs):
        head = f"近 {hours_txt} 小时温度 {t_first:.1f}→{t_last:.1f}℃ 单调上升"
    elif diffs and all(d < 0 for d in diffs):
        head = f"近 {hours_txt} 小时温度 {t_first:.1f}→{t_last:.1f}℃ 单调下降"
    else:
        head = f"近 {hours_txt} 小时温度 {min(temps):.1f}~{max(temps):.1f}℃"

    parts = [_failure_text(c) for c in criteria if not c["passed"]]
    reason = _failure_reason(criteria, temps)

    return f"{head}，" + "；".join(parts) + f" → 判定为不稳定（{reason}）"


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
# 渐进漂移选窗（S2）
# --------------------------------------------------------------------------

class Row(NamedTuple):
    """选窗时唯一允许看到的数据形状。

    刻意不含 sim_scenario：字段根本不存在，选窗时想用也用不上。
    """

    timestamp: datetime
    temperature: float
    humidity: float
    lux: float


def project_rows(samples: list) -> list:
    """把 B02 样本投影成只有 timestamp/temperature/humidity/lux 的行。

    source_type / sim_scenario 在这一步被丢掉，之后的选窗与判断都拿不到它们。
    """
    return [
        Row(
            timestamp=s.timestamp,
            temperature=s.temperature,
            humidity=s.humidity,
            lux=s.lux,
        )
        for s in samples
    ]


def pick_drifting_window(all_samples: list, reference: dict | None = None,
                         window_size: int = 6,
                         temp_tolerance: float = 0.8) -> dict:
    """挑一个"末点与 S1 可比、且窗口内温度全程单调上升"的窗口（渐进漂移）。

    候选必须同时满足：
      a. 末点温度落在 [S1 末点温度, S1 末点温度 + 容差]
         （单侧带：末点须已达到 S1 的水平且高出不超过容差。
          若改成完全对称的 |差| ≤ 容差，会多出 2026-10-20 09:00→11:30
          ——末点 32.2℃、差 0.6℃、ΔT 8.8℃——并胜出，与 S2 的验收窗口不符。）
      b. 窗口内温度全程单调上升
      c. judge_stability 判定为不稳定
    候选之间取 ΔT 最大的那个。

    只读 timestamp/temperature/humidity/lux 四列数值，不读 sim_scenario。
    reference 不传时，现场用 S1 的选窗策略算出参考末点温度。

    Returns:
        {
            "selected": {...} | None,
            "reason": str,
            "candidate_count": int,
            "rejected_count": int,
            "candidates": [...],
            "reference": {...},
        }
    """
    rows = project_rows(all_samples)

    if reference is None:
        reference = s1_reference(rows)

    ref_temp = reference["end_temperature"]
    band = (ref_temp, ref_temp + temp_tolerance)
    band_txt = (round(band[0], 1), round(band[1], 1))

    candidates = []
    rejected = []

    for start in range(0, len(rows) - window_size + 1):
        win = rows[start:start + window_size]
        temps = [r.temperature for r in win]
        t_first, t_last = temps[0], temps[-1]
        delta_t = round(max(temps) - min(temps), 2)
        base = {
            "window_index": start,
            "start_time": win[0].timestamp.isoformat(),
            "end_time": win[-1].timestamp.isoformat(),
            "end_temperature": t_last,
            "delta_t": delta_t,
        }

        rising = all(temps[i + 1] > temps[i] for i in range(len(temps) - 1))
        if not rising:
            rejected.append({**base, "reject": "温度未全程单调上升"})
            continue

        if not (band[0] - 1e-9 <= t_last <= band[1] + 1e-9):
            gap = round(t_last - ref_temp, 2)
            rejected.append({
                **base,
                "reject": (f"末点温度 {t_last}℃ 不在 S1 参考带 "
                           f"[{band_txt[0]}, {band_txt[1]}]℃（相差 {gap:+.1f}℃）"),
            })
            continue

        judged = judge_stability(win, rich_failure=True)
        if judged["passed"]:
            rejected.append({**base, "reject": "judge_stability 判定为稳定"})
            continue

        candidates.append({
            **base,
            "temp_range": delta_t,
            "passed": judged["passed"],
            "points": [
                {
                    "timestamp": r.timestamp.isoformat(),
                    "temperature": r.temperature,
                    "humidity": r.humidity,
                    "lux": r.lux,
                }
                for r in win
            ],
            "criteria": judged["criteria"],
            "conclusion": judged["conclusion"],
        })

    if not candidates:
        return {
            "selected": None,
            "reason": ("没有满足『末点温度与 S1 可比 + 全程单调上升 + 判定不稳定』的窗口"),
            "candidate_count": 0,
            "rejected_count": len(rejected),
            "candidates": [],
            "reference": {
                "s1_end_time": reference["end_time"],
                "s1_end_temperature": ref_temp,
                "temperature_band": [band_txt[0], band_txt[1]],
                "tolerance": temp_tolerance,
            },
        }

    selected = max(candidates, key=lambda c: (c["delta_t"], c["end_time"]))

    reason = (
        f"共 {len(rows) - window_size + 1} 个连续 {window_size} 点窗口，"
        f"满足条件的有 {len(candidates)} 个；条件为："
        f"(a) 末点温度落在 S1 参考带 [{band_txt[0]}, {band_txt[1]}]℃"
        f"（S1 末点 {ref_temp}℃，容差 {temp_tolerance}℃）；"
        f"(b) 窗口内温度全程单调上升；"
        f"(c) judge_stability 判定为不稳定；"
        f"最终取 ΔT 最大者 → ΔT={selected['delta_t']}℃，"
        f"窗口 {selected['start_time']} → {selected['end_time']}"
    )

    return {
        "selected": selected,
        "reason": reason,
        "candidate_count": len(candidates),
        "rejected_count": len(rejected),
        "candidates": [
            {k: c[k] for k in ("start_time", "end_time", "delta_t", "end_temperature")}
            for c in candidates
        ],
        "reference": {
            "s1_end_time": reference["end_time"],
            "s1_end_temperature": ref_temp,
            "temperature_band": [band_txt[0], band_txt[1]],
            "tolerance": temp_tolerance,
        },
    }


def s1_reference(rows: list) -> dict:
    """取 S1 的参考窗口与末点温度，作为"当前值相近"的基准。

    基准不是写死的数值，而是现场按 S1 的选窗策略算出来，
    S1 选窗变了 S2 的基准跟着变，两者永远可比。
    """
    picked = pick_stable_window(rows, window_size=6)
    sel = picked["selected"]
    if sel is None:
        raise RuntimeError(f"S1 选窗失败：{picked['reason']}")

    end_time = datetime.fromisoformat(sel["end_time"])
    end_row = next(r for r in rows if r.timestamp == end_time)
    return {
        "start_time": sel["start_time"],
        "end_time": sel["end_time"],
        "end_temperature": end_row.temperature,
        "end_humidity": end_row.humidity,
        "end_lux": end_row.lux,
        "reason": picked["reason"],
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
