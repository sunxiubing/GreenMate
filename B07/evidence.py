"""B07 Step 1（第二步）— 当前状态与最近历史 + 时间锚点

职责：
  1. 用 B02.api.history.get_environment_history(start, end) 显式传起止时间取历史窗口
  2. 取 anchor_time 那一刻的样本作为"当前状态"
  3. 另调 B05.test_functions.get_current_environment()，记为 latest_in_db（仅参考）
  4. 校验历史窗口完整性，不通过直接抛错，不静默修正

为什么不用 B05.test_functions.get_environment_history(hours=...)：
  它以墙钟时间（datetime.now）为结束时间，在本项目的模拟时间轴上会返回 0 行。

硬约束：
  - 不修改 B02 / B05 / B06 任何文件
  - 不引入新数据来源（不接天气 / 知识库 / 摄像头）
  - 不调用 LLM、不加载模型
  - 不修改数据库任何数据

用法：
  python B07/evidence.py                          # 用默认 anchor（库末点前 9.5h）
  python B07/evidence.py --anchor 2026-10-21T16:00:00
  python B07/evidence.py --json
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from B07.config import (
    DEFAULT_LOCATION,
    HISTORY_POINTS,
    SAMPLE_INTERVAL_MINUTES,
    HISTORY_SPAN,
    GAP_WARNING_HOURS,
)


class EvidenceValidationError(ValueError):
    """证据校验失败。直接抛出，不静默修正。"""


# --------------------------------------------------------------------------
# 内部工具
# --------------------------------------------------------------------------

def _sample_to_dict(s) -> dict:
    """Sample → dict，source_type 原样透传，不做任何改写。"""
    return {
        "timestamp": s.timestamp.isoformat(),
        "temperature": s.temperature,
        "humidity": s.humidity,
        "lux": s.lux,
        "source_type": s.source_type,
        "sim_scenario": s.sim_scenario,
    }


def _require_source_type(sample, ctx: str) -> str:
    """source_type 必须存在且非空，取不到就报错。"""
    st = getattr(sample, "source_type", None)
    if st is None or st == "":
        raise EvidenceValidationError(
            f"{ctx}: source_type 缺失或为空，不允许填默认值"
        )
    return st


# --------------------------------------------------------------------------
# 核心
# --------------------------------------------------------------------------

def build_evidence(anchor_time: datetime, location: str = DEFAULT_LOCATION) -> dict:
    """构造分析证据包。

    Args:
        anchor_time: 时间锚点（本次分析的"当前"时刻）
        location:   位置标识，仅作为时间锚点的一部分

    Returns:
        证据包 dict（结构见模块文档）

    Raises:
        EvidenceValidationError: 任一校验不通过
    """
    from B02.api.history import get_environment_history

    warnings: list[str] = []

    # ------------------------------------------------------------------
    # 1. 历史窗口：显式传起止时间
    # ------------------------------------------------------------------
    history_start = anchor_time - HISTORY_SPAN
    history_end = anchor_time

    history_samples = get_environment_history(history_start, history_end)

    # --- 校验 a：点数 == 6 ---
    if len(history_samples) != HISTORY_POINTS:
        actual_start = history_samples[0].timestamp.isoformat() if history_samples else "N/A"
        actual_end = history_samples[-1].timestamp.isoformat() if history_samples else "N/A"
        raise EvidenceValidationError(
            f"历史窗口点数校验失败：期望 {HISTORY_POINTS}，实际 {len(history_samples)}\n"
            f"  请求区间: {history_start.isoformat()} → {history_end.isoformat()}\n"
            f"  实际区间: {actual_start} → {actual_end}"
        )

    # --- 校验 b：相邻点间隔 == 30 分钟 ---
    for i in range(len(history_samples) - 1):
        t1 = history_samples[i].timestamp
        t2 = history_samples[i + 1].timestamp
        gap_min = (t2 - t1).total_seconds() / 60.0
        if gap_min != SAMPLE_INTERVAL_MINUTES:
            raise EvidenceValidationError(
                f"历史窗口采样间隔校验失败：第 {i+1}→{i+2} 点间隔 "
                f"{gap_min:.1f} 分钟，期望 {SAMPLE_INTERVAL_MINUTES} 分钟\n"
                f"  {t1.isoformat()} → {t2.isoformat()}"
            )

    # --- 校验 c：最后一个点 timestamp == anchor_time ---
    last_ts = history_samples[-1].timestamp
    if last_ts != anchor_time:
        raise EvidenceValidationError(
            f"历史窗口终点校验失败：末点 {last_ts.isoformat()} != anchor_time "
            f"{anchor_time.isoformat()}"
        )

    # --- 校验 e：source_type 原样透传 ---
    for i, s in enumerate(history_samples):
        _require_source_type(s, f"历史窗口第 {i+1} 点")

    # ------------------------------------------------------------------
    # 2. 当前状态 = anchor_time 那一刻的样本
    # ------------------------------------------------------------------
    current_samples = get_environment_history(anchor_time, anchor_time)
    if not current_samples:
        raise EvidenceValidationError(
            f"当前状态取不到：{anchor_time.isoformat()} 时刻没有样本"
        )
    current = current_samples[0]

    # --- 校验 d：current_state.timestamp == anchor_time ---
    if current.timestamp != anchor_time:
        raise EvidenceValidationError(
            f"当前状态时间戳校验失败：{current.timestamp.isoformat()} != anchor_time "
            f"{anchor_time.isoformat()}"
        )

    # --- 校验 e（当前状态） ---
    _require_source_type(current, "当前状态")

    current_state = _sample_to_dict(current)

    # ------------------------------------------------------------------
    # 3. 库内最新状态（B05 envelope 原样保留，仅作参考）
    # ------------------------------------------------------------------
    from B05.test_functions import get_current_environment as _b05_current
    latest_in_db = _b05_current()

    # ------------------------------------------------------------------
    # 4. hours_gap：anchor_time 与库末点的间隔
    # ------------------------------------------------------------------
    if latest_in_db.get("status") == "ok":
        db_last_ts = datetime.fromisoformat(latest_in_db["timestamp"])
        hours_gap = round((db_last_ts - anchor_time).total_seconds() / 3600.0, 2)
    else:
        db_last_ts = None
        hours_gap = None
        warnings.append(
            f"B05 get_current_environment 返回 status={latest_in_db.get('status')}，"
            f"无法计算 hours_gap"
        )

    if hours_gap is not None:
        if hours_gap > GAP_WARNING_HOURS:
            warnings.append(
                f"当前状态与库内最新状态相隔 {hours_gap} 小时"
                f"（超过 {GAP_WARNING_HOURS} 小时阈值），"
                f"本次分析锚点落后于库末点"
            )
        elif hours_gap < 0:
            warnings.append(
                f"当前状态时间锚点 {anchor_time.isoformat()} 晚于库内最新状态"
                f"（gap={hours_gap} 小时）"
            )

    # ------------------------------------------------------------------
    # 组装
    # ------------------------------------------------------------------
    return {
        "analysis_time": anchor_time.isoformat(),
        "location": location,
        "current_state": current_state,
        "latest_in_db": latest_in_db,
        "hours_gap": hours_gap,
        "history": [_sample_to_dict(s) for s in history_samples],
        "history_window": {
            "start": history_start.isoformat(),
            "end": history_end.isoformat(),
            "count": len(history_samples),
        },
        "sources": {
            "B02": "environment_samples",
            "B05": "get_current_environment",
        },
        "warnings": warnings,
    }


# --------------------------------------------------------------------------
# 主程序
# --------------------------------------------------------------------------

def _default_anchor() -> datetime:
    """默认锚点：库末点往前 9.5 小时（落在稳定白天窗口末尾）。

    仅用于 CLI 演示；真实调用方应显式传 anchor_time。
    """
    from B02.api.history import get_current_environment
    last = get_current_environment()
    if last is None:
        raise EvidenceValidationError("库内无数据，无法推导默认 anchor")
    return last.timestamp - timedelta(hours=9, minutes=30)


def main():
    import argparse
    import json

    parser = argparse.ArgumentParser(description="B07 Step 1: 当前状态与历史证据")
    parser.add_argument("--anchor", type=str, default=None,
                        help="时间锚点，ISO 格式，如 2026-10-21T16:00:00")
    parser.add_argument("--location", type=str, default=DEFAULT_LOCATION,
                        help="位置标识")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出")
    args = parser.parse_args()

    if args.anchor:
        anchor = datetime.fromisoformat(args.anchor)
    else:
        anchor = _default_anchor()

    print("=" * 70)
    print("B07 Step 1 — 当前状态与最近历史 + 时间锚点")
    print("=" * 70)
    print(f"\n  anchor_time: {anchor.isoformat()}")
    print(f"  location:    {args.location}")

    try:
        ev = build_evidence(anchor, args.location)
    except EvidenceValidationError as e:
        print(f"\n  ❌ 证据校验失败:\n  {e}")
        sys.exit(1)

    if args.json:
        print(json.dumps(ev, ensure_ascii=False, indent=2))
        return

    print(f"\n【校验】全部通过 ✅")
    print(f"  a. 历史点数 = {ev['history_window']['count']}（期望 {HISTORY_POINTS}）")
    print(f"  b. 相邻点间隔 = {SAMPLE_INTERVAL_MINUTES} 分钟（逐对检查）")
    print(f"  c. 末点 timestamp == anchor_time")
    print(f"  d. current_state.timestamp == anchor_time")
    print(f"  e. source_type 原样透传（{ev['current_state']['source_type']}）")
    print(f"  f. hours_gap = {ev['hours_gap']}")

    print("\n" + "=" * 70)
    print("【当前状态 current_state】")
    print("=" * 70)
    cs = ev["current_state"]
    print(f"  timestamp:     {cs['timestamp']}")
    print(f"  temperature:   {cs['temperature']}℃")
    print(f"  humidity:      {cs['humidity']}%")
    print(f"  lux:           {cs['lux']}")
    print(f"  source_type:   {cs['source_type']}")
    print(f"  sim_scenario:  {cs['sim_scenario']}")

    print("\n" + "=" * 70)
    print("【库内最新 latest_in_db（仅参考，非本次分析的当前）】")
    print("=" * 70)
    lid = ev["latest_in_db"]
    print(f"  status:    {lid.get('status')}")
    print(f"  source:    {lid.get('source')}")
    print(f"  timestamp: {lid.get('timestamp')}")
    if lid.get("status") == "ok":
        d = lid["data"]
        print(f"  data:      {d['temperature']}℃ / {d['humidity']}% / {d['lux']} lux "
              f"[{d['sim_scenario']}]")
    print(f"  hours_gap: {ev['hours_gap']} 小时（当前状态落后库末点）")

    print("\n" + "=" * 70)
    print("【历史窗口 history】")
    print("=" * 70)
    hw = ev["history_window"]
    print(f"  start: {hw['start']}   end: {hw['end']}   count: {hw['count']}")
    print(f"\n  {'timestamp':<26} {'温度℃':>8} {'湿度%':>8} {'lux':>10} {'source':>10} {'scenario':>12}")
    print(f"  {'-'*26} {'-'*8} {'-'*8} {'-'*10} {'-'*10} {'-'*12}")
    for h in ev["history"]:
        print(f"  {h['timestamp']:<26} {h['temperature']:>8.1f} {h['humidity']:>8.1f} "
              f"{h['lux']:>10.0f} {h['source_type']:>10} {h['sim_scenario']:>12}")

    print("\n" + "=" * 70)
    print("【warnings】")
    print("=" * 70)
    if ev["warnings"]:
        for w in ev["warnings"]:
            print(f"  ⚠️  {w}")
    else:
        print("  无")

    print("\n" + "=" * 70)
    print("B07 Step 1（第二步）完成 ✅")
    print("=" * 70)


if __name__ == "__main__":
    main()
