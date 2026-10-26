"""B07 S4-1 —— 离线"未来降温"场景的外部条件证据

S4 要造一组**方向不完全一致**的证据：内部有 WATCH 信号，外部却是"转好"。
真实天气此刻大概率是 deteriorating，拿不到"转好"方向，所以这里用**离线夹具**
构造一条 external_improving 的外部条件。

规矩：

  1. **复用 S3 已建立的离线假天气能力，不重写一套**。
     直接用 `B07.s3.OFFLINE_CASES["improving"]` 与 `B07.s3._make_offline_weather`
     —— S3 已经用这套夹具验证过 deteriorating / stable / improving 三个分支，
     本模块只是把它用在 S4 的锚点上。

  2. **不得把任何具体天气数值硬编码进产品代码**。
     本模块里**没有任何天气数值字面量**：PP、气温、云量、时刻全部来自
     S3 的夹具定义（B07/s3.py 里的构造用例区）。夹具数值只存在于
     离线实验代码里，不进产品逻辑。

  3. **判定完全走 B07.weather.build_external_context**，
     不自己写第二套判定 —— 否则两套判据会漂移。
     `signal` 是程序层算出来的，不是本模块断言的。

  4. **真实组也照跑并如实记录**。真实 API 此刻返回什么方向就是什么方向
     （大概率是 deteriorating），这也是合法结果。两个方向都记下来，
     不许只留好看的那个。

硬约束：

  - 不改 B01 / B02 / B03 / B04 / B05 / B06 任何文件
  - 不改 SYSTEM_PROMPT、不改固定问题、不改判据阈值、不重训模型
  - 不接进任何既有流程（本轮只产出可独立调用的能力）
  - 不写死任何实测天气数值

用法：

    from B07.s4_offline import build_offline_cooling_context, build_real_context
    ctx = build_offline_cooling_context(anchor)
    print(ctx["signal"])       # external_improving

自检（打印离线组 + 真实组的 external_context）：

    python -m B07.s4_offline
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from B07.s4_windows import selected_anchor

#: S4 冲突场景的默认锚点 —— **不硬编码**。
#: 唯一真源是 B07.s4_windows.selected_anchor()，它跑真实的选窗流程
#: （B06 真实输出 WATCH + 两套 DTW 参考集一致 + 内部状态不稳定）。
#: 之前这里曾硬编码一个时刻，会与选窗逻辑两处漂移，已改为动态解析。
_SCAN_CACHE = {}


def _get_scan() -> dict:
    """扫描结果缓存 —— 选窗很贵（要跑 331 个窗口 × 2 套 DTW），一次跑够。"""
    if "scan" not in _SCAN_CACHE:
        from B07.s4_windows import scan_watch_windows
        _SCAN_CACHE["scan"] = scan_watch_windows()
    return _SCAN_CACHE["scan"]


def default_anchor() -> str:
    """S4 冲突场景的历史锚点（ISO 字符串），实测选窗结果。"""
    if "anchor" not in _SCAN_CACHE:
        _SCAN_CACHE["anchor"] = selected_anchor(_get_scan())
    return _SCAN_CACHE["anchor"]


#: 外部天气取数参数（与 S3 一致）
WEATHER_LOCATION = "河北固安"
WEATHER_WINDOW = "6h"

#: 离线组用的构造用例名（S3 已有的三档之一）
OFFLINE_CASE = "improving"

#: 构造用例的说明 —— 报告引用时必须带着这句，不许当实测结论用
OFFLINE_CASE_LABEL = "构造用例（不是实测）"


# --------------------------------------------------------------------------
# 离线"转好"（未来降温）场景
# --------------------------------------------------------------------------

def build_offline_cooling_context(anchor: datetime | str | None = None,
                                  location: str = WEATHER_LOCATION,
                                  forecast_window: str = WEATHER_WINDOW) -> dict:
    """用 S3 的离线夹具构造"未来降温 / 外部转好"的外部条件上下文。

    夹具数值不在本模块，全部来自 B07.s3.OFFLINE_CASES。

    Args:
        anchor:        分析时刻（默认 S4 选出的 WATCH 窗口末点）。
        location:      取数地点。
        forecast_window: 未来窗口。

    Returns:
        {"context": <build_external_context 的原样返回>,
         "raw_forecast": <同一次假实现下的原始天气返回（S4-2 取 provider 用）>,
         "case": "improving",
         "is_constructed": True,
         "label": "构造用例（不是实测）",
         "note": <说明文字>}
    """
    from B07.s3 import (OFFLINE_CASES, _install_fake_weather, _make_offline_weather,
                        _make_unavailable_weather)
    from B07.weather import build_external_context, weather_unavailable_reason

    if anchor is None:
        anchor = default_anchor()
    if isinstance(anchor, str):
        anchor = datetime.fromisoformat(anchor)

    case = OFFLINE_CASES[OFFLINE_CASE]

    # S5-1：降级开关打开时，provider 层直接返回"不可用"。
    # 判定逻辑只有 B07.weather.weather_unavailable_reason() 一处；
    # 这里只是把同一个 provider 换成它这次失败的样子 ——
    # provider 身份不变（Offline-Fixture），区别只在返回了什么。
    reason = weather_unavailable_reason()
    if reason:
        provider_impl = _make_unavailable_weather(reason)
    else:
        provider_impl = _make_offline_weather(case)

    restore = _install_fake_weather(None, provider_impl)
    try:
        ctx = build_external_context(anchor, location, forecast_window)
        # S4-2：同一次假实现下再取一次原始天气返回，
        # 用来拿 provider（证据身份的一部分）。不新增数据来源、不联网。
        from B05.test_functions import get_weather_forecast
        raw_forecast = get_weather_forecast(location, forecast_window)
    finally:
        restore()

    return {
        "context": ctx,
        "raw_forecast": raw_forecast,
        "case": OFFLINE_CASE,
        "is_constructed": True,
        "label": OFFLINE_CASE_LABEL,
        "degraded": bool(reason),
        "note": (
            f"外部条件来自 S3 的离线夹具（case={OFFLINE_CASE}），"
            f"**{OFFLINE_CASE_LABEL}**。夹具数值定义在 B07/s3.py 的 "
            f"OFFLINE_CASES 里，本模块不含任何天气数值字面量。"
            f"signal 由 B07.weather.build_external_context 算出，"
            f"不是本模块断言的。"
            + (f" 本次 provider 返回不可用：{reason}" if reason else "")
        ),
    }


# --------------------------------------------------------------------------
# 真实组
# --------------------------------------------------------------------------

def build_real_context(anchor: datetime | str | None = None,
                       location: str = WEATHER_LOCATION,
                       forecast_window: str = WEATHER_WINDOW) -> dict:
    """真实调用 B05 → B03 Open-Meteo，取当前真实外部条件。

    此刻真实方向大概率是 deteriorating —— 那是合法结果，照实记录。

    Returns:
        {"context": <build_external_context 的原样返回>,
         "is_constructed": False,
         "label": "实测（真实 API）",
         "note": <说明文字>}
    """
    from B07.weather import build_external_context

    if anchor is None:
        anchor = default_anchor()
    if isinstance(anchor, str):
        anchor = datetime.fromisoformat(anchor)

    ctx = build_external_context(anchor, location, forecast_window)

    return {
        "context": ctx,
        "case": "real",
        "is_constructed": False,
        "label": "实测（真实 API）",
        "note": (
            "外部条件来自真实 Open-Meteo API（经 B05.get_weather_forecast）。"
            "真实方向此刻是什么就是什么 —— 与离线组方向不同也是合法结果，"
            "两组都记录，不许只留其中一个。"
        ),
    }


# --------------------------------------------------------------------------
# 冲突场景汇总
# --------------------------------------------------------------------------

def build_conflict_scene(anchor: datetime | str | None = None) -> dict:
    """把"内部 WATCH + 外部转好"这一组方向不一致的证据凑齐。

    这一轮**只产出证据**，不送进主流程、不调 LLM、不做最终判断 ——
    判断由后续阶段基于这组证据来做。

    Returns:
        {"anchor": str,
         "internal": {...来自 B07.s4_windows 的选窗结果...},
         "external_offline": {...},
         "external_real": {...},
         "knowledge": {...},
         "direction_conflict": {...}}
    """
    from B07.knowledge import DEFAULT_QUERY, search_knowledge
    from B07.s4_windows import pick_watch_window

    if anchor is None:
        anchor = default_anchor()
    if isinstance(anchor, str):
        anchor = datetime.fromisoformat(anchor)

    scan = _get_scan()
    picked = pick_watch_window(scan)
    offline = build_offline_cooling_context(anchor)
    real = build_real_context(anchor)
    kb = search_knowledge(DEFAULT_QUERY)

    internal_label = (picked["selected"] or {}).get("prod_label")
    off_signal = offline["context"].get("signal")
    real_signal = real["context"].get("signal")

    conflict = {
        "internal_model_label": internal_label,
        "external_offline_signal": off_signal,
        "external_real_signal": real_signal,
        "has_direction_conflict": internal_label == "WATCH"
        and off_signal == "external_improving",
        "explanation": (
            f"内部：B06 在选中的窗口上真实输出 {internal_label}；"
            f"外部（离线构造用例）：{off_signal}；"
            f"外部（真实 API）：{real_signal}。"
            f"离线组内部与外部方向相反 —— 这正是 S4 要处理的不一致。"
        ),
    }

    return {
        "anchor": anchor.isoformat(),
        "internal": picked,
        "external_offline": offline,
        "external_real": real,
        "knowledge": kb,
        "direction_conflict": conflict,
    }


# --------------------------------------------------------------------------
# 自检入口
# --------------------------------------------------------------------------

def main() -> int:
    import json

    print("=" * 78)
    print("B07 S4-1 —— 离线『未来降温』场景的外部条件证据")
    print("=" * 78)

    print(f"\n锚点：{default_anchor()}")
    print(f"（该锚点由 B07.s4_windows 实测选出："
          f"B06 真实输出 WATCH，两套 DTW 参考集一致，内部状态不稳定）")

    print("\n" + "=" * 78)
    print("[1] 离线组 —— 构造用例『转好 / 未来降温』")
    print("=" * 78)
    off = build_offline_cooling_context()
    print(f"  标注：{off['label']}")
    print(f"  说明：{off['note']}")
    print(json.dumps(off["context"], ensure_ascii=False, indent=2))
    print(f"\n  signal       = {off['context']['signal']}")
    print(f"  judge_method = {off['context']['judge_method']}")
    print(f"  precip_delta = {off['context']['precip_delta']} pp")
    print(f"  temp_delta   = {off['context']['temp_delta']} ℃")
    print(f"  cloud_delta  = {off['context']['cloud_delta']} %")

    print("\n" + "=" * 78)
    print("[2] 真实组 —— 真实 Open-Meteo API（方向是什么就是什么）")
    print("=" * 78)
    real = build_real_context()
    print(f"  标注：{real['label']}")
    print(json.dumps(real["context"], ensure_ascii=False, indent=2))
    print(f"\n  signal       = {real['context']['signal']}")
    print(f"  judge_method = {real['context']['judge_method']}")
    print(f"  precip_delta = {real['context']['precip_delta']} pp")
    print(f"  temp_delta   = {real['context']['temp_delta']} ℃")

    print("\n" + "=" * 78)
    print("[3] 方向不一致核对")
    print("=" * 78)
    scene = build_conflict_scene()
    print(json.dumps(scene["direction_conflict"], ensure_ascii=False, indent=2))

    print("\n" + "=" * 78)
    ok = True
    if off["context"]["signal"] != "external_improving":
        print(f"❌ 离线组 signal 不是 external_improving，"
              f"实际 {off['context']['signal']}")
        ok = False
    else:
        print("✅ 离线组 signal = external_improving")
    if scene["direction_conflict"]["has_direction_conflict"]:
        print("✅ 内部 WATCH 与外部转好方向确实不一致")
    else:
        print("❌ 方向不一致未成立")
        ok = False
    print("=" * 78)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
