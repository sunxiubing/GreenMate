"""B07 Step 1（第四步 A）— 结构化判断（纯程序，不经过 LLM）

职责：
  把 S1-3 的 run_bundle（evidence + model_output）折算成五个结构化字段：
    current_environment / recent_trend / predicted_attention /
    evidence_status / limitations

  S3-1 起**可选**追加第 6 个字段 external_conditions（外部天气条件）：
    - 没有外部条件时，输出**严格是原来的 5 个字段**，键名、顺序、取值全不变
      （S1 / S2 的既有断言依赖这一点）
    - 有外部条件时，在 5 个字段之后**追加**第 6 个（追加，不是替换）

硬约束：
  - 不接 LLM，不调用任何模型
  - 五个字段必须能脱离 LLM 文本单独存在，且能被重新算出来
    （recompute_judgement 用同样输入必须算出同样结果）
  - 规则阈值写在模块顶部常量，不硬编码在函数体里
  - limitations 的每一条都要从 run_bundle 里的真实数据算出来

用法：
  python B07/judgement.py --anchor 2026-10-21T16:00:00
"""

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))


# --------------------------------------------------------------------------
# 规则阈值（模块顶部常量）
# --------------------------------------------------------------------------
MODEL_INPUT_SPAN_HOURS = 3        # B06 模型输入窗口跨度（6 点 @30min）
MODEL_TARGET_SPAN_HOURS = 1       # B06 模型预测目标跨度（2 点 @30min）
MODEL_METRIC_RANGE_NOTE = "温度 / 湿度 / 光照三项指标，范围取自 B02 训练数据分布"

METRICS_UNAVAILABLE = "未获取"     # 指标取不到时的占位文本

# limitations 至少要覆盖的四条
LIMITATION_KEYS = [
    "simulated_data",
    "model_boundary",
    "time_anchor",
    "no_weather_no_kb",
]

# 稳定性判据阈值（与 B07/stability.py 一致，用于复述具体数值）
STABILITY_TEMP_RANGE_MAX = 2.0
STABILITY_HUMI_TOTAL_MAX = 3.0
STABILITY_HUMI_STEP_MAX = 1.5
STABILITY_LUX_JUMP_MAX = 15000
STABILITY_NO_MONOTONE = True


# --------------------------------------------------------------------------
# 工具
# --------------------------------------------------------------------------

def _history_samples(run_bundle: dict) -> list:
    """把 run_bundle["history"]（dict 列表）转成 B07 可用的 Sample 列表。"""
    from B06.SPEC import Sample
    from datetime import datetime

    history = run_bundle.get("history") or []
    samples = []
    for h in history:
        samples.append(Sample(
            timestamp=datetime.fromisoformat(h["timestamp"]),
            temperature=h["temperature"],
            humidity=h["humidity"],
            lux=h["lux"],
            source_type=h.get("source_type", ""),
            sim_scenario=h.get("sim_scenario", ""),
        ))
    return samples


def _judge_stability(run_bundle: dict) -> dict:
    """重新跑一遍 S1-1 的稳定判定，保证 judgement 可脱离 LLM 复算。"""
    from B07.stability import judge_stability
    samples = _history_samples(run_bundle)
    if not samples:
        return {"passed": False, "criteria": [], "conclusion": "无历史数据，无法判定"}
    rich = _rich_failure(run_bundle)
    return judge_stability(samples, rich_failure=rich)


def _rich_failure(run_bundle: dict) -> bool:
    """该 run 是否使用富化失败结论句（由场景决定；S1 保持原文本）。"""
    try:
        from B07.scenarios import rich_failure_for
        return rich_failure_for(run_bundle.get("scenario"))
    except Exception:
        return False


def _tail_window_stability() -> tuple[bool, str]:
    """检查库末点所在的 6 点窗口是否满足稳定判据。

    用于 limitation③ 的原因说明——不是写死的字符串，是实测出来的。
    """
    try:
        from B02.api.history import get_environment_history
        from B07.stability import judge_stability
        from datetime import datetime

        last = get_environment_history(datetime(2026, 10, 15, 2), datetime(2026, 10, 22, 23))
        if len(last) < 6:
            return False, "库末点历史点不足 6 点，无法复核"
        tail = last[-6:]
        judged = judge_stability(tail)
        return judged["passed"], judged["conclusion"]
    except Exception as e:
        return False, f"复核失败：{type(e).__name__}: {e}"


def _fmt(v, digits=1):
    """数值格式化，None 安全。"""
    if v is None:
        return METRICS_UNAVAILABLE
    if isinstance(v, float):
        return f"{v:.{digits}f}"
    return str(v)


# --------------------------------------------------------------------------
# 五个字段
# --------------------------------------------------------------------------

def _current_environment(run_bundle: dict) -> dict:
    """字段①：当前环境状态。"""
    cs = run_bundle.get("current_state") or {}
    return {
        "temperature": cs.get("temperature"),
        "humidity": cs.get("humidity"),
        "lux": cs.get("lux"),
        "source_type": cs.get("source_type"),
        "sim_scenario": cs.get("sim_scenario"),
        "sampled_at": cs.get("timestamp"),
        "unit": {"temperature": "℃", "humidity": "%", "lux": "lux"},
    }


def _recent_trend(run_bundle: dict) -> dict:
    """字段②：近期趋势（引用 S1-1 稳定判据里的具体数值与结论）。"""
    judged = _judge_stability(run_bundle)
    criteria = judged.get("criteria", [])
    verdict = "稳定" if judged.get("passed") else "不稳定"

    key_numbers = {}
    for c in criteria:
        item = {
            "value": c["value"],
            "threshold": c["threshold"],
            "passed": c["passed"],
            "note": c["note"],
        }
        # 富化失败结论里的"超出幅度"一并带出来，
        # 这样 LLM 复述这个数字时不会被判成"事实越界"
        if "excess" in c:
            item["excess"] = c["excess"]
        key_numbers[c["name"]] = item

    history = run_bundle.get("history") or []
    first = history[0] if history else {}
    last = history[-1] if history else {}

    return {
        "window_span": f"{MODEL_INPUT_SPAN_HOURS} 小时 / {len(history)} 点",
        "window_start": first.get("timestamp"),
        "window_end": last.get("timestamp"),
        "stability_verdict": verdict,
        "conclusion": judged.get("conclusion"),
        "criteria": key_numbers,
        "thresholds_used": {
            "temp_range_max": STABILITY_TEMP_RANGE_MAX,
            "humi_total_max": STABILITY_HUMI_TOTAL_MAX,
            "humi_step_max": STABILITY_HUMI_STEP_MAX,
            "lux_jump_max": STABILITY_LUX_JUMP_MAX,
            "require_no_monotone": STABILITY_NO_MONOTONE,
        },
    }


def _predicted_attention(run_bundle: dict) -> str:
    """字段③：原样照抄 model_output，不做任何加工。"""
    mo = run_bundle.get("model_output") or {}
    return mo.get("predicted_attention")


def _evidence_status(run_bundle: dict) -> dict:
    """字段④：当前证据状态（有哪些证据、来自哪一环、是否齐全）。"""
    mo = run_bundle.get("model_output") or {}
    evidence = run_bundle.get("history") or []
    cs = run_bundle.get("current_state") or {}
    lid = run_bundle.get("latest_in_db") or {}

    items = [
        {
            "module": "B02",
            "item": "current_state",
            "present": bool(cs),
            "detail": (f"{cs.get('temperature')}℃ / {cs.get('humidity')}% / {cs.get('lux')} lux"
                       f" @ {cs.get('timestamp')}") if cs else "缺失",
        },
        {
            "module": "B02",
            "item": "history",
            "present": len(evidence) > 0,
            "detail": f"{len(evidence)} 点，{evidence[0]['timestamp'] if evidence else 'N/A'}"
                      f" → {evidence[-1]['timestamp'] if evidence else 'N/A'}",
        },
        {
            "module": "B05",
            "item": "latest_in_db",
            "present": lid.get("status") == "ok",
            "detail": f"status={lid.get('status')}，库末点 {lid.get('timestamp')}",
        },
        {
            "module": "B06",
            "item": "model_output",
            "present": bool(mo),
            "detail": (f"model_version={mo.get('model_version')}，"
                       f"predicted_attention={mo.get('predicted_attention')}，"
                       f"confidence={mo.get('confidence')}") if mo else "缺失",
        },
        {
            "module": "B06",
            "item": "dtw_similarity",
            "present": mo.get("dtw_temperature") is not None,
            "detail": (f"dtw T={mo.get('dtw_temperature')} / H={mo.get('dtw_humidity')}"
                       f" / L={mo.get('dtw_lux')}") if mo else "缺失",
        },
        {
            "module": "B01",
            "item": "llm_explanation",
            "present": False,
            "detail": ("不计入本环节的齐全性判定；LLM 解释在 B07/explain.py 独立环节、"
                       "于本环节之后生成"),
        },
    ]

    required = [i for i in items if i["module"] != "B01"]
    complete = all(i["present"] for i in required)

    # 措辞要说清楚"齐全"是截至哪个环节：
    # B01 的 LLM 解释在结构化判断之后才生成，本环节判不了、也不该判。
    if complete:
        summary = (f"截至判断环节证据齐全（B02 {sum(1 for i in items if i['module'] == 'B02')}/2、"
                   f"B05 {sum(1 for i in items if i['module'] == 'B05')}/1、"
                   f"B06 {sum(1 for i in items if i['module'] == 'B06')}/2）；"
                   f"B01 解释在本环节之后生成")
    else:
        summary = ("截至判断环节证据不齐全：" + "、".join(
            f"{i['module']}.{i['item']}" for i in required if not i["present"])
            + "；B01 解释在本环节之后生成")

    return {
        "items": items,
        "required_modules": sorted({i["module"] for i in required}),
        "missing_modules": sorted({i["module"] for i in required if not i["present"]}),
        "complete": complete,
        "summary": summary,
    }


def _limitations(run_bundle: dict, ledger=None) -> dict:
    """字段⑤：当前判断限制（四条基础 + 证据不可用时追加的条目）。

    S5-1：基础四条**一个字不改**（S1/S2/S3 的存档必须逐字段可比）。
    只有当证据台账里**确实有取不到的证据**时，才追加一条
    "evidence_unavailable" 限制 —— 内容全部来自台账的真实 status/error。
    没有台账、或台账里全部证据可用时，本函数输出与原来完全一致。
    """
    mo = run_bundle.get("model_output") or {}
    meta = run_bundle.get("model_meta") or {}
    metrics = meta.get("metrics") or {}

    # ① 数据是模拟数据
    cs = run_bundle.get("current_state") or {}
    source_type = cs.get("source_type") or "未知"
    sim_data = {
        "key": "simulated_data",
        "text": (f"本次全部环境数据 source_type='{source_type}'，"
                 f"为模拟数据，非真实传感器读数；"
                 f"所有结论只在这套模拟时间轴上成立，不可外推到真实大棚。"),
        "source_type": source_type,
    }

    # ② 模型适用边界
    recall = metrics.get("recall")
    f1 = metrics.get("f1")
    accuracy = metrics.get("accuracy")
    precision = metrics.get("precision")
    metrics_text = (
        f"accuracy={_fmt(accuracy, 3)}、precision={_fmt(precision, 3)}、"
        f"recall={_fmt(recall, 3)}、f1={_fmt(f1, 3)}"
        if metrics else METRICS_UNAVAILABLE
    )
    model_boundary = {
        "key": "model_boundary",
        "text": (f"B06 模型（{meta.get('model_version', METRICS_UNAVAILABLE)}，"
                 f"标签规则 {meta.get('label_source', METRICS_UNAVAILABLE)}）"
                 f"只适用 {MODEL_INPUT_SPAN_HOURS}h → {MODEL_TARGET_SPAN_HOURS}h 的窗口，"
                 f"且只覆盖{MODEL_METRIC_RANGE_NOTE}；"
                 f"其测试集指标为 {metrics_text}，"
                 f"recall {_fmt(recall, 2)} / f1 {_fmt(f1, 2)} 表明对异常窗口存在明显漏报，"
                 f"预测为 NORMAL 不等于确认无异常。"),
        "model_version": meta.get("model_version"),
        "recall": recall,
        "f1": f1,
    }

    # ③ 时间锚点
    hours_gap = run_bundle.get("hours_gap")
    analysis_time = run_bundle.get("analysis_time")
    latest_ts = (run_bundle.get("latest_in_db") or {}).get("timestamp")
    tail_stable, tail_conclusion = _tail_window_stability()
    reason = (
        "库末点所在的 3 小时窗口不满足稳定判据（实测："
        f"{tail_conclusion}）"
        if not tail_stable else
        "库末点窗口满足稳定判据，但选窗策略仍优先更早的稳定白天窗口"
        f"（实测：{tail_conclusion}）"
    )
    time_anchor = {
        "key": "time_anchor",
        "text": (f"本次 analysis_time = {analysis_time}，"
                 f"库内最新状态 latest_in_db = {latest_ts}，"
                 f"两者相隔 hours_gap = {hours_gap} 小时；"
                 f"锚点选在更早位置的原因：{reason}。"
                 f"因此本次判断描述的是 {analysis_time} 时刻的状态，"
                 f"不是库末点时刻的状态。"),
        "analysis_time": analysis_time,
        "latest_in_db": latest_ts,
        "hours_gap": hours_gap,
        "tail_window_stable": tail_stable,
        "tail_window_conclusion": tail_conclusion,
    }

    # ④ 模块边界：B04 未接入；B03 外部条件是**可选**的
    #
    # 这条限制的文字在"接了外部条件"和"没接"两种运行里必须**完全一样**：
    #   - 结构化判断的前 5 个字段要能逐字段对比，接了外部条件不许改动它们；
    #   - 如果这里写死"未接入 B03 天气"，接了外部条件时它就是**假话**，
    #     而 LLM 会照着它说"当前判断未接入真实天气"——自相矛盾的状态会
    #     把外部条件直接压掉，实验就白做了。
    # 所以这里只写**恒为真的边界**：以 B02 历史 + B06 预测为主、B04 未接入、
    # 外部条件（若接入）只作方向性参考。
    no_weather_no_kb = {
        "key": "no_weather_no_kb",
        "text": ("本阶段的判断以 B02 历史 + B06 预测为主；"
                 "B04 农艺知识库未接入，"
                 "因此无法解释『为什么该数值对番茄意味着什么』；"
                 "外部信息只作方向性参考，"
                 "不参与内部数值的判定，其数值也不能与内部模拟值直接比较。"),
        "connected_modules": ["B02", "B05", "B06"],
        "not_connected_modules": ["B04"],
        "optional_modules": ["B03"],
    }

    out = {
        "simulated_data": sim_data,
        "model_boundary": model_boundary,
        "time_anchor": time_anchor,
        "no_weather_no_kb": no_weather_no_kb,
        "all_keys": list(LIMITATION_KEYS),
    }

    # ---- S5-1：证据不可用时的限制条目 ----
    extra = _unavailable_evidence_limitation(ledger)
    if extra:
        out["evidence_unavailable"] = extra
        out["all_keys"] = list(out["all_keys"]) + ["evidence_unavailable"]

    return out


def _unavailable_evidence_limitation(ledger) -> dict | None:
    """台账里有取不到的证据时，给限制字段追加一条。

    内容全部来自台账的真实 status / error —— 不写死文案里的数字，
    也不把"取不到"说成"没有这项信息"。

    没有台账、台账里没有不可用证据、或所有不可用证据都没被本次判断采用时，
    返回 None（此时限制字段与原来一字不差）。
    """
    if ledger is None:
        return None

    from B07.evidence_ledger import adopted_ledger_entries

    try:
        entries = adopted_ledger_entries(ledger)
    except Exception:
        return None

    bad = [e for e in entries if (e.get("status") or "") != "ok"]
    if not bad:
        return None

    parts = []
    for e in bad:
        ext = e.get("ext") or {}
        who = f"{e.get('source_type')}"
        if ext.get("provider"):
            who += f"（provider={ext.get('provider')}）"
        parts.append(
            f"{who} 的证据当前不可用（status={e.get('status')}"
            + (f"，失败原因：{e.get('error')}" if e.get("error") else "")
            + "）")

    return {
        "key": "evidence_unavailable",
        "text": ("以下证据源当前不可用，与它们相关的判断受到限制："
                 + "；".join(parts)
                 + "。其余仍可用的证据（B02 历史、B06 预测等）继续参与本次判断，"
                 "本次结论只在这些证据能支撑的范围内成立。"
                 "回答中必须明确说明这一限制。"),
        "unavailable_sources": [
            {
                "source_type": e.get("source_type"),
                "source": e.get("source"),
                "status": e.get("status"),
                "error": e.get("error"),
            }
            for e in bad
        ],
    }


# --------------------------------------------------------------------------
# 公开接口
# --------------------------------------------------------------------------

# 用来区分"没传 external_conditions"和"显式传了 None"
_EXTERNAL_UNSET = object()

# external_conditions 的稳定字段名（顺序即输出顺序）
EXTERNAL_FIELDS = [
    "signal",
    "baseline_point",
    "forecast_point",
    "precip_delta",
    "temp_delta",
    "cloud_delta",
    "solar_radiation",
    "judge_method",
    "basis",
    "caveats",
]


def _external_conditions(external: dict, identity: dict | None = None) -> dict:
    """把 B07.weather.build_external_context() 的结果整成稳定字段。

    字段名固定不变，取值来自真实数据；外部条件不可用时 signal 为
    external_unavailable、judge_method 为 unavailable，不做任何粉饰。

    identity: 证据身份（S4-2）。给了就把 provider / forecast_time 一并带出，
    它们取自**台账里那条 B03 证据的 ext**，与台账逐值相等 ——
    这样"这份外部条件是哪一条证据"只有一处定义。
    """
    if not isinstance(external, dict):
        return {
            "signal": None,
            "baseline_point": None,
            "forecast_point": None,
            "precip_delta": None,
            "temp_delta": None,
            "cloud_delta": None,
            "solar_radiation": None,
            "judge_method": None,
            "basis": None,
            "caveats": [],
        }

    out = {
        "signal": external.get("signal"),
        "baseline_point": external.get("baseline_point"),
        "forecast_point": external.get("forecast_point"),
        "precip_delta": external.get("precip_delta"),
        "temp_delta": external.get("temp_delta"),
        "cloud_delta": external.get("cloud_delta"),
        "solar_radiation": external.get("solar_radiation"),
        "judge_method": external.get("judge_method"),
        "basis": external.get("basis"),
        "caveats": list(external.get("caveats") or []),
    }
    # 附加信息（不属于稳定字段，但一起带出，便于回溯时核对）
    out["available"] = external.get("available")
    out["future_ok"] = external.get("future_ok")
    out["warnings"] = list(external.get("warnings") or [])
    out["location"] = external.get("location")
    out["forecast_window"] = external.get("forecast_window")
    out["analysis_time"] = external.get("analysis_time")
    # S5-1：证据状态显式化 —— "不可用"和"没有外部信息"是两回事，
    # 前者必须说得出是谁不可用、为什么不可用。
    status = external.get("status")
    if status is None:
        status = "ok" if external.get("available") else "unavailable"
    out["status"] = status
    out["error"] = external.get("error")
    if identity:
        # S4-2：证据身份随外部条件一起带出（与台账 B03 证据的 ext 逐值相等）
        out["provider"] = identity.get("provider")
        out["forecast_time"] = identity.get("forecast_time")
    return out


def build_judgement(run_bundle: dict, external_conditions=_EXTERNAL_UNSET,
                    evidence_ledger=None) -> dict:
    """由 run_bundle 生成结构化判断。

    Args:
        run_bundle: S1-3 predict_for_run() 的返回值
        external_conditions: 可选的第 6 个字段内容
            （B07.weather.build_external_context() 的返回值）。
            不传时从 run_bundle.get("external_conditions") 取；
            显式传 None 表示"确定没有外部条件"。
        evidence_ledger: 可选的证据台账（B07.evidence_ledger.EvidenceLedger
            或它的可序列化 dict）。给了才会追加第 7 个字段
            evidence_divergence；不给时字段集合与 S1/S2/S3 **完全一致**。

    Returns:
        没有外部条件时，恰好五个顶层字段（键名、顺序、取值全不变）：
        current_environment / recent_trend / predicted_attention /
        evidence_status / limitations

        有外部条件时，在上述 5 个字段之后**追加**第 6 个：
        external_conditions

        有证据台账时，再**追加**第 7 个：evidence_divergence
    """
    if external_conditions is _EXTERNAL_UNSET:
        external_conditions = run_bundle.get("external_conditions")
    if evidence_ledger is None:
        evidence_ledger = run_bundle.get("evidence_ledger")

    judgement = {
        "current_environment": _current_environment(run_bundle),
        "recent_trend": _recent_trend(run_bundle),
        "predicted_attention": _predicted_attention(run_bundle),
        "evidence_status": _evidence_status(run_bundle),
        "limitations": _limitations(run_bundle, evidence_ledger),
    }

    # S4-2：给了台账，外部条件**从台账里的 B03 证据派生**，
    # 不在这里另写一套天气字段的组装逻辑（同一份证据的身份只定义一次）。
    identity = None
    if evidence_ledger is not None:
        from B07.evidence_ledger import weather_context_from_ledger

        weather = weather_context_from_ledger(evidence_ledger)
        if weather is not None:
            external_conditions = weather.get("context")
            identity = {
                "provider": weather.get("provider"),
                "forecast_time": weather.get("forecast_time"),
            }

    if external_conditions is not None:
        judgement["external_conditions"] = _external_conditions(
            external_conditions, identity)

    # S4-2：第 7 个字段（可选追加）。不传台账时**根本不会出现**这个字段，
    # 所以 S1 / S2 / S3 的存档逐字段复算结果一字不变。
    if evidence_ledger is not None:
        judgement["evidence_divergence"] = build_evidence_divergence(evidence_ledger)

    return judgement


def recompute_judgement(run_bundle: dict, external_conditions=_EXTERNAL_UNSET,
                        evidence_ledger=None) -> dict:
    """用同样输入重新算一遍同样的字段（用于自检）。"""
    return build_judgement(run_bundle, external_conditions, evidence_ledger)


def self_check(run_bundle: dict, external_conditions=_EXTERNAL_UNSET,
               evidence_ledger=None) -> dict:
    """自检：复算结果是否与 build_judgement 逐字段相等。"""
    a = build_judgement(run_bundle, external_conditions, evidence_ledger)
    b = recompute_judgement(run_bundle, external_conditions, evidence_ledger)

    fields = list(a.keys())
    diffs = []
    for f in fields:
        if a[f] != b[f]:
            diffs.append(f)
    # 顺序也要一致
    same_order = list(a.keys()) == list(b.keys())

    # 有没有第 6 个字段取决于有没有外部条件；
    # 有没有第 7 个字段取决于有没有证据台账。
    expected_count = 5
    if "external_conditions" in fields:
        expected_count += 1
    if "evidence_divergence" in fields:
        expected_count += 1

    return {
        "fields": fields,
        "field_count": len(fields),
        "expected_field_count": expected_count,
        "identical": not diffs and same_order,
        "differing_fields": diffs,
        "same_order": same_order,
        "passed": (not diffs) and same_order and len(fields) == expected_count,
    }


# --------------------------------------------------------------------------
# 证据分歧（S4-2，第 7 个结构化字段）
# --------------------------------------------------------------------------
#
# 关键设计：**direction（证据属性，单值）与三列表（判断分项）分开**。
#
#   direction  ∈ {supporting, opposing, undetermined}
#       —— 每条证据一个方向，由程序规则给出（B07/evidence_ledger.judge_direction），
#          不经过 LLM。
#   三列表    supporting / opposing / undetermined
#       —— 装的是**判断项**，不是原始证据；每条都必须带来源归属。
#
# 为什么要分开：一条证据可以既"支持提高关注"（方向），
# 又"仍不足以确定"（限定）。知识证据就是这种：
#   方向 = supporting（它提供"决策基于不完整信息可能导致作物受损"的依据）
#   限定 = 只支持"要关注"，不能证明未来一定恶化
# 这条限定**不是它的 direction**，但必须出现在 undetermined 列表里。
#
# 三列表只装**被本次判断采用**的证据（adopted=True）。
# 调用过但没被采用的工具结果留在台账里，不进这三张表。

#: 有冲突时的结论句（固定文本，逐字不改）
DIVERGENCE_RESOLUTION_CONFLICT = (
    "存在方向不一致的证据，系统保留分歧，不做强制合并，也不做投票；"
    "请按各来源分别判断"
)
#: 无冲突时的结论句
DIVERGENCE_RESOLUTION_CONSISTENT = "各证据方向一致"

#: note 的固定说明
DIVERGENCE_NOTE = (
    "方向由程序规则判定（B07/evidence_ledger.judge_direction），不经过 LLM；"
    "三列装的是判断项，不是原始证据；"
    "NORMAL 不等于确认无异常；系统不做投票，也不做强制合并。"
)


def build_evidence_divergence(ledger) -> dict:
    """由证据台账算出第 7 个字段 evidence_divergence。

    Args:
        ledger: B07.evidence_ledger.EvidenceLedger 或其可序列化 dict。

    Returns:
        {
          "has_conflict": bool,
          "supporting":    [ {source_type, source, time, summary, basis}, ... ],
          "opposing":     [ ... ],
          "undetermined": [ {source_type, source, time, summary, reason}, ... ],
          "each_source":  {source_type: direction},
          "resolution":   str,
          "note":         str,
        }
    """
    from B07.evidence_ledger import (
        DIRECTION_OPPOSING,
        DIRECTION_SUPPORTING,
        adopted_ledger_entries,
        basis_of,
        limitations_of,
        summarize,
        undetermined_reason,
    )

    entries = adopted_ledger_entries(ledger)

    supporting: list[dict] = []
    opposing: list[dict] = []
    undetermined: list[dict] = []
    each_source: dict[str, str] = {}

    for entry in entries:
        source_type = entry.get("source_type")
        direction = entry.get("direction")
        each_source[source_type] = direction

        base = {
            "source_type": source_type,
            "source": entry.get("source"),
            "time": entry.get("time"),
            "summary": summarize(entry),
        }

        if direction == DIRECTION_SUPPORTING:
            supporting.append({**base, "basis": basis_of(entry)})
        elif direction == DIRECTION_OPPOSING:
            opposing.append({**base, "basis": basis_of(entry)})
        else:
            undetermined.append({**base, "reason": undetermined_reason(entry)})

        # 该证据自带的"仍不足以确定"的限定（与方向分开）
        for lim in limitations_of(entry):
            undetermined.append({
                **base,
                "summary": lim["summary"],
                "reason": lim["reason"],
            })

    has_conflict = bool(supporting) and bool(opposing)
    resolution = (DIVERGENCE_RESOLUTION_CONFLICT if has_conflict
                  else DIVERGENCE_RESOLUTION_CONSISTENT)

    return {
        "has_conflict": has_conflict,
        "supporting": supporting,
        "opposing": opposing,
        "undetermined": undetermined,
        "each_source": each_source,
        "resolution": resolution,
        "note": DIVERGENCE_NOTE,
    }


# --------------------------------------------------------------------------
# 合成关注等级（内部 B06 预测 × 外部天气信号）
# --------------------------------------------------------------------------
#
# 这是**独立于结构化判断五/六字段**的一层：它不进 build_judgement 的输出，
# 因此 S1 / S2 的"5 字段"不变量不受影响；它作为存档里的一个独立字段存在，
# 可从存档完全复算。
#
# 规则（S3 完成线第三条，写死在程序里，不靠 LLM 措辞）：
#     内部 predicted_attention == "WATCH"                → watch_internal
#     内部 NORMAL 且 signal == "external_deteriorating"  → watch_external
#     内部 NORMAL 且 signal ∈ {stable, improving, unknown} → internal_only

COMBINED_WATCH_INTERNAL = "watch_internal"
COMBINED_WATCH_EXTERNAL = "watch_external"
COMBINED_INTERNAL_ONLY = "internal_only"

# S4-2 新增：证据方向不一致，且系统**不强行合并**
COMBINED_CONFLICT_UNRESOLVED = "conflict_unresolved"

# 没有外部条件时，外部信号按"未知"处理
EXTERNAL_SIGNAL_UNKNOWN = "unknown"


def combined_attention(predicted_attention: str | None,
                        external_signal: str | None = None,
                        evidence_divergence: dict | None = None) -> str:
    """把内部 B06 预测与外部天气信号合成一个关注等级（纯程序，不经过 LLM）。

    S4-2 起新增一条**分支**：证据台账给出 evidence_divergence 且
    has_conflict 为真时，返回 "conflict_unresolved"。

      - 现有三条规则**一个字都没改**，只是多了一个前置分支；
      - 触发条件依赖 evidence_divergence 的**存在** ——
        S1 / S2 / S3 的存档里没有这个字段，输出与原来逐值相同；
      - "conflict_unresolved" **不是错误**，它表示"证据方向不一致，
        系统保留分歧而不强行合并"。

    为什么放在最前面：冲突本身就是要传达的结论。若先按内部/外部规则出结果，
    等于把分歧强行合并掉了 —— 那正是这个分支要避免的事。
    """
    from B07.weather import SIGNAL_DETERIORATING

    if evidence_divergence and evidence_divergence.get("has_conflict"):
        return COMBINED_CONFLICT_UNRESOLVED

    if predicted_attention == "WATCH":
        return COMBINED_WATCH_INTERNAL
    if external_signal == SIGNAL_DETERIORATING:
        return COMBINED_WATCH_EXTERNAL
    return COMBINED_INTERNAL_ONLY


def _find_evidence_divergence(run_bundle: dict) -> dict | None:
    """从 run_bundle / 存档里找到 evidence_divergence（找不到就是 None）。"""
    if not isinstance(run_bundle, dict):
        return None
    div = run_bundle.get("evidence_divergence")
    if isinstance(div, dict):
        return div
    for key in ("structured_judgement", "judgement"):
        holder = run_bundle.get(key)
        if isinstance(holder, dict):
            div = holder.get("evidence_divergence")
            if isinstance(div, dict):
                return div
    return None


def build_combined_attention(run_bundle: dict) -> dict:
    """合成关注等级 + 判定理由。

    run_bundle 只需要两个键：model_output（含 predicted_attention）与
    external_conditions（可为 None / 缺省）。存档里也是这两个键，
    所以回溯时可以用同一份存档直接复算。

    S4-2：若 run_bundle（含存档）里有 evidence_divergence 且
    has_conflict 为真，走 conflict_unresolved 分支。
    """
    mo = run_bundle.get("model_output") or {}
    internal = mo.get("predicted_attention")

    ext = run_bundle.get("external_conditions")
    signal = ext.get("signal") if isinstance(ext, dict) else None
    if not signal:
        signal = EXTERNAL_SIGNAL_UNKNOWN

    divergence = _find_evidence_divergence(run_bundle)

    value = combined_attention(internal, signal, divergence)

    if value == COMBINED_CONFLICT_UNRESOLVED:
        div = divergence or {}
        reason = (f"证据方向不一致（支持 {len(div.get('supporting') or [])} 条 / "
                  f"相反 {len(div.get('opposing') or [])} 条），"
                  f"系统保留分歧，不做强制合并，也不做投票")
    elif value == COMBINED_WATCH_INTERNAL:
        reason = (f"内部 B06 预测为 {internal}，按内部信号直接提升关注度；"
                  f"外部信号 {signal} 不改变这一结论")
    elif value == COMBINED_WATCH_EXTERNAL:
        reason = (f"内部 B06 预测为 {internal}，但外部条件判定为 {signal}，"
                  f"关注度由外部信号抬升")
    else:
        reason = (f"内部 B06 预测为 {internal}，外部信号为 {signal}"
                  f"（无外部信号，或外部条件平稳/好转），关注度保持内部结论")

    return {
        "value": value,
        "internal_predicted_attention": internal,
        "external_signal": signal,
        "has_conflict": bool((divergence or {}).get("has_conflict")),
        "reason": reason,
    }


# --------------------------------------------------------------------------
# 主程序
# --------------------------------------------------------------------------

def main():
    import argparse
    import json
    from datetime import datetime

    parser = argparse.ArgumentParser(description="B07 Step 1: 结构化判断")
    parser.add_argument("--anchor", type=str, default="2026-10-21T16:00:00")
    parser.add_argument("--location", type=str, default="A区-3号温室")
    parser.add_argument("--model", type=str,
                        default=str(_PROJECT_ROOT / "B06" / "artifacts" / "b06-20261020-v1"))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    print("=" * 70)
    print("B07 Step 1（第四步 A）— 结构化判断（纯程序，无 LLM）")
    print("=" * 70)

    from B07.evidence import build_evidence
    from B07.predict import predict_for_run

    anchor = datetime.fromisoformat(args.anchor)
    evidence = build_evidence(anchor, args.location)
    run_bundle = predict_for_run(evidence, args.model)

    judgement = build_judgement(run_bundle)
    check = self_check(run_bundle)

    if args.json:
        print(json.dumps({"judgement": judgement, "self_check": check},
                         ensure_ascii=False, indent=2))
        return

    print("\n【五个结构化字段】")
    print("=" * 70)

    print("\n① current_environment:")
    for k, v in judgement["current_environment"].items():
        if k != "unit":
            print(f"     {k:<16}: {v}")

    print("\n② recent_trend:")
    rt = judgement["recent_trend"]
    print(f"     window_span:     {rt['window_span']}")
    print(f"     window:          {rt['window_start']} → {rt['window_end']}")
    print(f"     stability:       {rt['stability_verdict']}")
    print(f"     conclusion:      {rt['conclusion']}")
    for name, c in rt["criteria"].items():
        icon = "✅" if c["passed"] else "❌"
        print(f"       {icon} {name}: 实测={c['value']} 阈值={c['threshold']} — {c['note']}")

    print("\n③ predicted_attention:")
    print(f"     {judgement['predicted_attention']}   （原样照抄 model_output，未加工）")

    print("\n④ evidence_status:")
    es = judgement["evidence_status"]
    for i in es["items"]:
        icon = "✅" if i["present"] else "⭕"
        print(f"     {icon} [{i['module']}] {i['item']:<18} {i['detail']}")
    print(f"     齐全: {es['complete']}   {es['summary']}")

    print("\n⑤ limitations:")
    for key in judgement["limitations"]["all_keys"]:
        print(f"     [{key}]")
        print(f"       {judgement['limitations'][key]['text']}")

    print("\n" + "=" * 70)
    print("【复算自检 recompute_judgement】")
    print("=" * 70)
    print(f"  字段数: {check['field_count']} / 期望 {check['expected_field_count']}")
    print(f"  字段列表: {check['fields']}")
    print(f"  逐字段相等: {not check['differing_fields']}   差异字段: {check['differing_fields'] or '无'}")
    print(f"  字段顺序一致: {check['same_order']}")
    print(f"  自检结果: {'✅ 通过' if check['passed'] else '❌ 未通过'}")

    print("\n" + "=" * 70)
    print(f"B07 Step 1（第四步 A）完成 {'✅' if check['passed'] else '❌'}")
    print("=" * 70)
    if not check["passed"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
