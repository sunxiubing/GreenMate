"""B07 S3-2 对照实验的不变量测试（全部离线，不联网）

这些测试锁住的是"实验本身可信"的几条底线：

  1. external_conditions 为 None → 结构化判断**严格是原来的 5 个字段**
  2. external_conditions 非 None → 恰好 6 个字段，且前 5 个**逐字段相等**
  3. 相对判定的边界：Δpp = 29.9 → stable；30.0 → deteriorating；
     -30.0 → improving；-29.9 → stable
  4. 基线点取不到 → 走绝对阈值兜底，judge_method = "absolute_fallback"
  5. 两个点都取不到 → signal = "external_unavailable"，且**不出现任何天气数值**
  6. 预报时刻早于 analysis_time → 不产生判定，也不返回 "stable"
  7. 提示词指纹与固定问题一字未改

硬约束：
  - 天气数据全部来自夹具，**不包含任何实测天气数值**
  - 不改测试去迁就实现

跑法：
  ./venv/Scripts/python.exe -m pytest B07/tests/test_s3_invariants.py -v
"""

from datetime import datetime

import pytest

ANCHOR = datetime.fromisoformat("2026-10-21T16:00:00")

# 夹具用的虚构时刻（不是实测值）
BASE_TS = "2026-10-24T22:00"
FORE_TS = "2026-10-25T04:00"


def _weather_result(window, timestamp, precip, temp=15.0, cloud=40.0,
                    status="ok", error=None):
    """造一个 B05 形状的天气返回值（全部虚构）。"""
    if status == "ok":
        data = {
            "location": "测试地点",
            "forecast_window": window,
            "timestamp": timestamp,
            "temperature": temp,
            "humidity": 70.0,
            "precipitation_probability": precip,
            "cloud_cover": cloud,
            "solar_radiation": 0.0,
            "weather_code": 0,
            "weather_description": "多云",
        }
    else:
        data = None
    return {
        "status": status,
        "provider": "Test-Fixture",
        "retrieved_at": "2026-10-24T00:00:00+00:00",
        "data": data,
        "error": error,
    }


@pytest.fixture
def fake_weather(monkeypatch):
    """替换 B05 的天气工具；state 里可以改各窗口的返回值。"""
    import B05.test_functions as tf

    calls: list[dict] = []
    state = {
        "now": _weather_result("now", BASE_TS, 40.0),
        "6h": _weather_result("6h", FORE_TS, 40.0),
    }

    def _fake(location, forecast_window="now", _inject=None):
        calls.append({"location": location, "forecast_window": forecast_window})
        return state.get(forecast_window) or state.get("now")

    monkeypatch.setattr(tf, "get_weather_forecast", _fake)
    return {"calls": calls, "state": state}


def _bundle():
    """evidence + model_output（不涉及 LLM、不涉及天气）。"""
    from B07.predict import predict_for_run
    from B07.evidence import build_evidence

    return predict_for_run(build_evidence(ANCHOR, "A区-3号温室"))


# --------------------------------------------------------------------------
# 1 & 2：字段数（5 / 6）
# --------------------------------------------------------------------------

def test_none_external_conditions_gives_exactly_five_fields():
    from B07.judgement import build_judgement

    judgement = build_judgement(_bundle(), None)
    assert list(judgement.keys()) == [
        "current_environment", "recent_trend", "predicted_attention",
        "evidence_status", "limitations",
    ]
    assert len(judgement) == 5
    assert "external_conditions" not in judgement


def test_non_none_external_conditions_gives_exactly_six_fields():
    from B07.judgement import build_judgement

    ext = {"signal": "external_stable", "judge_method": "relative_delta",
           "basis": "夹具构造", "caveats": []}
    judgement = build_judgement(_bundle(), ext)
    assert len(judgement) == 6
    assert list(judgement.keys())[-1] == "external_conditions"


def test_six_field_first_five_are_field_by_field_equal():
    """接外部条件**只追加**，前 5 个字段一个字节都不许变。"""
    from B07.judgement import build_judgement

    bundle = _bundle()
    five = build_judgement(bundle, None)
    ext = {"signal": "external_deteriorating", "judge_method": "relative_delta",
           "basis": "夹具构造", "caveats": ["夹具"]}
    six = build_judgement(bundle, ext)

    assert list(six.keys())[:5] == list(five.keys())
    for field in five:
        assert six[field] == five[field], f"字段 {field} 被外部条件改动了"


# --------------------------------------------------------------------------
# 3：相对判定的边界
# --------------------------------------------------------------------------

@pytest.mark.parametrize("base_pp,fore_pp,expected", [
    (40.0, 69.9, "external_stable"),        # Δ=+29.9
    (40.0, 70.0, "external_deteriorating"),  # Δ=+30.0
    (40.0, 10.0, "external_improving"),      # Δ=-30.0
    (40.0, 10.1, "external_stable"),         # Δ=-29.9
])
def test_relative_delta_boundaries(fake_weather, base_pp, fore_pp, expected):
    from B07.weather import build_external_context

    fake_weather["state"]["now"] = _weather_result("now", BASE_TS, base_pp)
    fake_weather["state"]["6h"] = _weather_result("6h", FORE_TS, fore_pp)

    ctx = build_external_context(ANCHOR, "测试地点", "6h")
    assert ctx["signal"] == expected
    assert ctx["judge_method"] == "relative_delta"


# --------------------------------------------------------------------------
# 4：基线点取不到 → 绝对阈值兜底
# --------------------------------------------------------------------------

def test_baseline_unavailable_uses_absolute_fallback(fake_weather):
    from B07.weather import build_external_context

    fake_weather["state"]["now"] = _weather_result(
        "now", BASE_TS, None, status="unavailable", error="基线取不到")
    fake_weather["state"]["6h"] = _weather_result("6h", FORE_TS, 75.0)

    ctx = build_external_context(ANCHOR, "测试地点", "6h")

    assert ctx["judge_method"] == "absolute_fallback"
    assert ctx["signal"] == "external_deteriorating"
    assert ctx["precip_delta"] is None
    assert any("基线" in w for w in ctx["warnings"])


# --------------------------------------------------------------------------
# 5：两个点都取不到
# --------------------------------------------------------------------------

def test_both_points_unavailable_is_explicit_and_fabricates_nothing(fake_weather):
    from B07.weather import build_external_context

    for w in ("now", "6h"):
        fake_weather["state"][w] = _weather_result(
            w, None, None, status="unavailable", error="天气服务不可用")

    ctx = build_external_context(ANCHOR, "测试地点", "6h")

    assert ctx["signal"] == "external_unavailable"
    assert ctx["judge_method"] == "unavailable"
    assert ctx["available"] is False
    assert ctx["baseline_point"] is None
    assert ctx["forecast_point"] is None
    # 不许编造任何天气数值
    raw = str(ctx)
    for banned in ("precipitation_probability\": ", "temperature\": ",
                   "cloud_cover\": "):
        assert banned not in raw


def test_unavailable_external_conditions_still_gives_six_fields(fake_weather):
    """外部不可用时，第 6 个字段仍然在（显式说明不可用），不是静默消失。"""
    from B07.judgement import build_judgement
    from B07.weather import build_external_context

    for w in ("now", "6h"):
        fake_weather["state"][w] = _weather_result(
            w, None, None, status="unavailable", error="天气服务不可用")

    ctx = build_external_context(ANCHOR, "测试地点", "6h")
    judgement = build_judgement(_bundle(), ctx)

    assert len(judgement) == 6
    ext = judgement["external_conditions"]
    assert ext["signal"] == "external_unavailable"
    assert ext["judge_method"] == "unavailable"


# --------------------------------------------------------------------------
# 6：预报时刻早于 analysis_time → 不产生判定
# --------------------------------------------------------------------------

def test_forecast_not_in_future_produces_no_judgement(fake_weather):
    from B07.weather import build_external_context

    # 预报时刻（2026-10-25T04:00）早于 analysis_time（2026-10-26T00:00）
    late_anchor = datetime.fromisoformat("2026-10-26T00:00:00")
    ctx = build_external_context(late_anchor, "测试地点", "6h")

    assert ctx["future_ok"] is False
    assert ctx["signal"] == "external_unavailable"
    assert ctx["judge_method"] == "unavailable"
    assert ctx["precip_delta"] is None
    assert ctx["signal"] != "external_stable"


def test_forecast_in_future_produces_judgement(fake_weather):
    from B07.weather import build_external_context

    ctx = build_external_context(ANCHOR, "测试地点", "6h")
    assert ctx["future_ok"] is True
    assert ctx["signal"] == "external_stable"
    assert ctx["judge_method"] == "relative_delta"


# --------------------------------------------------------------------------
# 7：提示词与固定问题未被改动
# --------------------------------------------------------------------------

def test_prompt_fingerprint_and_fixed_question_unchanged():
    from B07.explain import FIXED_QUESTION, SYSTEM_PROMPT

    import hashlib

    prompt_sha = hashlib.sha256(SYSTEM_PROMPT.encode("utf-8")).hexdigest()[:12]
    question_sha = hashlib.sha256(FIXED_QUESTION.encode("utf-8")).hexdigest()[:12]

    assert prompt_sha == "aa1400cf8568"
    assert question_sha == "d3cc394bfcbe"
    assert FIXED_QUESTION == ("分析当前番茄生长环境状态以及接下来值得关注的变化，"
                              "并说明判断依据。")


def test_external_block_is_the_only_context_difference(fake_weather):
    """带/不带外部天气时，LLM 上下文的差异只能是 ⑥ 块。"""
    from B07.explain import format_judgement_context
    from B07.weather import build_external_context

    bundle = _bundle()
    ctx_a = format_judgement_context(bundle)

    ext = build_external_context(ANCHOR, "测试地点", "6h")
    bundle["external_conditions"] = ext
    ctx_b = format_judgement_context(bundle)

    assert ctx_b.startswith(ctx_a + "\n\n")
    added = ctx_b[len(ctx_a) + 2:]
    assert added.startswith("【⑥ 外部天气条件")
    assert "【⑥" not in ctx_a


def test_unavailable_external_block_tells_llm_not_to_fabricate(fake_weather):
    from B07.explain import format_judgement_context
    from B07.weather import build_external_context

    for w in ("now", "6h"):
        fake_weather["state"][w] = _weather_result(
            w, None, None, status="unavailable", error="天气服务不可用")

    bundle = _bundle()
    ext = build_external_context(ANCHOR, "测试地点", "6h")
    bundle["external_conditions"] = ext
    ctx = format_judgement_context(bundle)

    assert "外部天气不可用" in ctx
    assert "不要编造天气" in ctx


# --------------------------------------------------------------------------
# 8：合成关注等级（程序规则，可脱离 LLM 复算）
# --------------------------------------------------------------------------

@pytest.mark.parametrize("internal,signal,expected", [
    ("WATCH", "external_deteriorating", "watch_internal"),
    ("WATCH", "external_stable", "watch_internal"),
    ("WATCH", None, "watch_internal"),
    ("NORMAL", "external_deteriorating", "watch_external"),
    ("NORMAL", "external_stable", "internal_only"),
    ("NORMAL", "external_improving", "internal_only"),
    ("NORMAL", "external_unavailable", "internal_only"),
    ("NORMAL", None, "internal_only"),
])
def test_combined_attention_rule(internal, signal, expected):
    """S3 完成线第三条：规则写死在程序里，不靠 LLM 措辞。"""
    from B07.judgement import combined_attention

    assert combined_attention(internal, signal) == expected


def test_combined_attention_uses_unknown_when_no_external_conditions():
    from B07.judgement import build_combined_attention

    ca = build_combined_attention({
        "model_output": {"predicted_attention": "NORMAL"},
        "external_conditions": None,
    })
    assert ca["value"] == "internal_only"
    assert ca["external_signal"] == "unknown"
    assert ca["internal_predicted_attention"] == "NORMAL"
    assert ca["reason"]


def test_combined_attention_is_not_a_structured_judgement_field():
    """合成关注等级是独立字段，不许变成结构化判断的第 7 个字段。"""
    from B07.judgement import build_judgement

    ext = {"signal": "external_deteriorating", "judge_method": "relative_delta",
           "basis": "夹具构造", "caveats": []}
    judgement = build_judgement(_bundle(), ext)
    assert len(judgement) == 6
    assert "combined_attention" not in judgement

