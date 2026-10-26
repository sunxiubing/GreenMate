"""B07 场景注册表 — S1 稳态基线 / S2 渐进漂移

场景之间只允许有一处不同：**看哪一段历史**。
问题、system prompt、模型、判据阈值、当前值口径全部保持一致，
这样 S1 与 S2 的输出才可以横向比较。

场景定义：
  S1  稳态基线     anchor_policy=stable_window
  S2  渐进漂移     anchor_policy=drifting_comparable_to_S1

硬约束：
  - 选窗只允许使用 temperature / humidity / lux 三列数值
    （B02/SPEC.py 明确禁止把 sim_scenario 用作选窗或判断依据）
  - 不新增数据来源：不接 B03 天气、不接 B04 知识库
  - 固定问题与 LLM system prompt 都不改
  - 不改 S1 的判据阈值

选窗的具体实现放在 B07/stability.py（pick_stable_window / pick_drifting_window），
本模块只负责"哪个场景用哪个策略"这一层。
"""

import sys
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from B07.explain import FIXED_QUESTION

# S2 末点温度相对 S1 末点温度的容差（℃）
S2_TEMP_TOLERANCE = 0.8
# 窗口点数（与 S1 一致：6 点 × 30 分钟 = 2.5 小时跨度）
WINDOW_SIZE = 6


# --------------------------------------------------------------------------
# 场景注册表
# --------------------------------------------------------------------------

SCENARIOS = {
    "S1": {
        "key": "S1",
        "title": "稳态基线",
        "anchor_policy": "stable_window",
        "expect_stability": True,
        # 是否使用"超了哪个阈值、超了多少"的富化失败结论句
        "rich_failure": False,
        # 与 S1 完全相同的字符串，一字不改
        "question": FIXED_QUESTION,
    },
    "S2": {
        "key": "S2",
        "title": "渐进漂移",
        "anchor_policy": "drifting_comparable_to_S1",
        # S2 的稳定性判据必须判为"不稳定"
        "expect_stability": False,
        "rich_failure": True,
        "question": FIXED_QUESTION,
    },
    # ---- S3：带/不带外部天气的对照实验 ----
    # 两个场景的锚点、问题、提示词、模型、判据阈值全部相同，
    # 唯一区别是 S3_with 的结构化判断里多一个 external_conditions 字段。
    "S3_without": {
        "key": "S3_without",
        "title": "S3 对照组：不接外部天气",
        "anchor_policy": "stable_window",
        "expect_stability": True,
        "rich_failure": False,
        "external_weather": False,
        "question": FIXED_QUESTION,
    },
    "S3_with": {
        "key": "S3_with",
        "title": "S3 实验组：接外部天气",
        "anchor_policy": "stable_window",
        "expect_stability": True,
        "rich_failure": False,
        "external_weather": True,
        "question": FIXED_QUESTION,
    },
    # ---- S4：证据分歧场景（内部 WATCH × 外部转好）----
    # 锚点不是"挑一个稳定窗口"，而是 S4-1 实测选出的那个
    # **B06 真实输出 WATCH** 的窗口（B07.s4_windows）。
    "S4": {
        "key": "S4",
        "title": "S4 证据分歧：内部 WATCH × 外部转好",
        "anchor_policy": "s4_watch_window",
        "expect_stability": False,
        "rich_failure": True,
        "question": FIXED_QUESTION,
    },
    # ---- S5：证据退化场景 ----
    # 场景定义与 S4 **一字不差**（同一锚点、同一窗口、同一判据、同一问题），
    # 唯一区别是本次运行里天气证据是否可用（见 B07/weather.py 的降级开关）。
    # 这里只是给存档一个自解释的标签，不改任何行为。
    "S5": {
        "key": "S5",
        "title": "S5 证据退化：天气证据不可用（与 S4 同场景）",
        "anchor_policy": "s4_watch_window",
        "expect_stability": False,
        "rich_failure": True,
        "question": FIXED_QUESTION,
    },
}


def get_scenario(key: str) -> dict:
    if key not in SCENARIOS:
        raise KeyError(f"未知场景 {key!r}，可选：{sorted(SCENARIOS)}")
    return SCENARIOS[key]


def rich_failure_for(scenario_key: str | None) -> bool:
    """该场景是否使用富化失败结论句。未知/缺省场景按 S1 处理（文本不变）。"""
    if not scenario_key:
        return False
    return bool(get_scenario(scenario_key).get("rich_failure"))


# --------------------------------------------------------------------------
# 投影与参考量（实现都在 B07/stability.py）
# --------------------------------------------------------------------------

def project_rows(samples: list) -> list:
    """把 B02 样本投影成只有 timestamp/temperature/humidity/lux 的行。"""
    from B07.stability import project_rows as _project
    return _project(samples)


def s1_reference(rows: list) -> dict:
    """S1 的参考窗口与末点温度（S2 的"当前值相近"基准）。"""
    from B07.stability import s1_reference as _ref
    return _ref(rows)


def select_drifting_window(rows: list, reference: dict | None = None,
                            window_size: int = WINDOW_SIZE,
                            temp_tolerance: float = S2_TEMP_TOLERANCE) -> dict:
    """S2 选窗（薄封装，实质逻辑在 B07/stability.pick_drifting_window）。"""
    from B07.stability import pick_drifting_window
    return pick_drifting_window(rows, reference=reference,
                                window_size=window_size,
                                temp_tolerance=temp_tolerance)


# --------------------------------------------------------------------------
# 统一入口
# --------------------------------------------------------------------------

def select_window(scenario_key: str, samples: list | None = None,
                  window_size: int = WINDOW_SIZE) -> dict:
    """按场景的 anchor_policy 选窗，返回统一结构。"""
    from B07.stability import load_all_samples, pick_stable_window, pick_drifting_window

    scenario = get_scenario(scenario_key)
    if samples is None:
        samples = load_all_samples()
    rows = project_rows(samples)

    if scenario["anchor_policy"] == "stable_window":
        picked = pick_stable_window(rows, window_size=window_size)
        if picked["selected"] is None:
            raise RuntimeError(f"没有可选的稳定窗口：{picked['reason']}")
        sel = picked["selected"]
        return {
            "anchor_time": datetime.fromisoformat(sel["end_time"]),
            "selected": sel,
            "reason": picked["reason"],
            "candidate_count": picked["candidate_count"],
            "daytime_count": picked["daytime_count"],
            "pool": picked["pool"],
            "total_samples": len(rows),
            "data_range": [rows[0].timestamp.isoformat(), rows[-1].timestamp.isoformat()],
        }

    if scenario["anchor_policy"] == "drifting_comparable_to_S1":
        result = pick_drifting_window(rows, window_size=window_size)
        result["anchor_time"] = datetime.fromisoformat(result["selected"]["end_time"])
        result["total_samples"] = len(rows)
        result["data_range"] = [rows[0].timestamp.isoformat(),
                                rows[-1].timestamp.isoformat()]
        return result

    if scenario["anchor_policy"] == "s4_watch_window":
        # S4 的锚点由 S4-1 的窗口扫描给出：B06 **真实输出 WATCH** 的窗口，
        # 内部状态本身不稳定，且两套 DTW 参考集判定一致。
        from B07.s4_windows import pick_watch_window, scan_watch_windows

        picked = pick_watch_window(scan_watch_windows())
        sel = picked.get("selected")
        if not sel:
            raise RuntimeError(
                f"S4 没有选出 WATCH 窗口：{picked.get('reason')}"
            )
        return {
            "anchor_time": datetime.fromisoformat(sel["end_time"]),
            "selected": sel,
            "reason": picked.get("reason"),
            "notes": picked.get("notes") or [],
            "fallback_to_s2": picked.get("fallback_to_s2"),
        }

    raise ValueError(f"未实现的 anchor_policy：{scenario['anchor_policy']}")


def check_expectation(scenario_key: str, judged: dict) -> None:
    """校验判定结果符合场景预期（S2 必须判为不稳定）。"""
    scenario = get_scenario(scenario_key)
    expect = scenario["expect_stability"]
    if bool(judged.get("passed")) != bool(expect):
        raise RuntimeError(
            f"{scenario_key} 期望 {'稳定' if expect else '不稳定'}，"
            f"实际判定 {'稳定' if judged.get('passed') else '不稳定'}"
        )
