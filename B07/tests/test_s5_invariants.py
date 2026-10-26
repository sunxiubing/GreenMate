"""B07 S5-1 的不变量测试（全部离线，不联网、不调 LLM）

锁住的是这几条底线（S5 文档 p.38–41）：

  1. 降级开关只有一个：B07_WEATHER_UNAVAILABLE，只认 "1"；
     不设 / 其它值 ⇒ 一切照旧（基线路径零影响）
  2. 不可用是**显式**的：status="unavailable"、result=None、error 非空
     —— 不许把 error 写成空串，不许把上次成功的值留成这次的结果
  3. 不可用证据的 direction = undetermined，**不许伪造成 supporting/opposing**
  4. 不可用证据**不进**分歧三列的 supporting / opposing，
     只在 ⑦ 块的"当前不可用的证据"一节里单独列
  5. ⑥ 块**不许整块消失**：provider 保留、forecast_time=None、status=unavailable
  6. 降级时 combined_attention **不再依赖外部方向**（不判 watch_external）
  7. 不许加任何 fallback：上下文里不许出现编造出来的天气数值
  8. 限制字段只在**确实有不可用证据**时追加 evidence_unavailable；
     没有时与原来一字不差（S1/S2/S3 的存档必须逐字段可比）
  9. 提示词与固定问题指纹全程未变

硬约束：
  - 不改测试去迁就实现
  - 天气/知识/模型全部用夹具，不含任何实测数值

跑法：
  ./venv/Scripts/python.exe -m pytest B07/tests/test_s5_invariants.py -v
"""

from datetime import datetime

import pytest

ANCHOR = datetime.fromisoformat("2026-10-21T09:30:00")
ANCHOR_ISO = "2026-10-21T09:30:00"

#: 提示词与固定问题的指纹（S1 起就固定，本轮一个字都没动）
SYSTEM_PROMPT_SHA12 = "aa1400cf8568"
FIXED_QUESTION_SHA12 = "d3cc394bfcbe"


# --------------------------------------------------------------------------
# 夹具
# --------------------------------------------------------------------------

def _history():
    return [
        {"timestamp": "2026-10-21T07:00:00", "temperature": 24.2, "humidity": 92.6, "lux": 21000.0},
        {"timestamp": "2026-10-21T07:30:00", "temperature": 25.8, "humidity": 88.1, "lux": 26000.0},
        {"timestamp": "2026-10-21T08:00:00", "temperature": 25.5, "humidity": 84.0, "lux": 30000.0},
        {"timestamp": "2026-10-21T08:30:00", "temperature": 27.7, "humidity": 76.0, "lux": 36000.0},
        {"timestamp": "2026-10-21T09:00:00", "temperature": 27.8, "humidity": 72.0, "lux": 40000.0},
        {"timestamp": "2026-10-21T09:30:00", "temperature": 28.6, "humidity": 66.9, "lux": 42492.0},
    ]


def _bundle():
    """一个最小但完整的 run_bundle（不调 B02 / B06，全部是夹具值）。"""
    return {
        "history": _history(),
        "current_state": {
            "temperature": 28.6, "humidity": 66.9, "lux": 42492.0,
            "source_type": "simulated", "sim_scenario": "cooling",
            "timestamp": ANCHOR_ISO,
        },
        "latest_in_db": {"status": "ok"},
        "model_output": {
            "predicted_attention": "WATCH",
            "confidence": 0.72,
            "model_version": "b06-20261020-v1",
            "window_id": "s1-live-001",
            "feature_count": 3,
        },
        "model_meta": {
            "model_version": "b06-20261020-v1",
            "label_source": "b06/spec",
            "metrics": {"accuracy": 0.745, "precision": 0.591,
                        "recall": 0.448, "f1": 0.510},
        },
    }


def _b03_ok():
    return {
        "available": True,
        "signal": "external_improving",
        "judge_method": "relative_delta",
        "basis": "夹具构造",
        "baseline_point": {"timestamp": "2026-10-24T22:00:00"},
        "forecast_point": {"timestamp": "2026-10-25T04:00:00"},
    }


def _b02_result(passed=False):
    return {
        "stability_passed": passed,
        "stability_conclusion": "夹具结论",
        "temps": [24.2, 25.8, 25.5, 27.7, 27.8, 28.6],
        "history": [],
        "current_state": {},
    }


def _b06_result(label="WATCH"):
    return {
        "predicted_attention": label,
        "confidence": 1.0,
        "model_version": "b06-20261020-v1",
        "window_id": "s1-live-001",
    }


def _b04_result():
    return {
        "query": "过期数据导致温室调控决策基于过时信息",
        "top_k": 5,
        "citations": [{"chunk_id": "CH-003-02", "source_id": "SRC-003", "score": 0.36}],
        "has_relevant": True,
        "note": "夹具命中",
    }


def _ledger(weather_available=True):
    """造一个台账：四类证据各一条，全部被采用。

    weather_available=False 时，B03 那条**取不到**（status=unavailable）。
    """
    from B07.evidence_ledger import EvidenceLedger
    from B07.evidence_registry import make_tool_result

    ledger = EvidenceLedger()
    ledger.record_call("B02.get_environment_history", {"start": "a", "end": "b"},
                       purpose="取历史窗口")
    ledger.add_evidence(
        make_tool_result(source="B02/environment_samples", status="ok",
                         data=_b02_result()),
        "B02", tool_name="B02.get_environment_history")

    ledger.record_call("B06.predict_window", {"window_id": "s1-live-001"},
                       purpose="B06 预测")
    ledger.add_evidence(
        make_tool_result(source="B06/b06-20261020-v1", status="ok",
                         data=_b06_result()),
        "B06", tool_name="B06.predict_window",
        ext={"metrics": {"recall": 0.448, "f1": 0.510}})

    ledger.record_call("B05.get_weather_forecast", {"location": "X"},
                       purpose="取外部条件")
    if weather_available:
        ledger.add_evidence(
            make_tool_result(source="B03/Open-Meteo", status="ok",
                             data=_b03_ok()),
            "B03", tool_name="B05.get_weather_forecast",
            ext={"provider": "Offline-Fixture",
                 "forecast_time": "2026-10-25T04:00:00"})
    else:
        ledger.add_evidence(
            make_tool_result(source="B03/Open-Meteo",
                             status="unavailable",
                             data=None,
                             error="weather provider unavailable "
                                   "(B07_WEATHER_UNAVAILABLE=1): 模拟天气 API 故障"),
            "B03", tool_name="B05.get_weather_forecast",
            ext={"provider": "Offline-Fixture", "forecast_time": None})

    ledger.record_call("B05.search_tomato_knowledge", {"query": "q"},
                       purpose="知识检索")
    ledger.add_evidence(
        make_tool_result(source="B04/knowledge_base", status="ok",
                         data=_b04_result()),
        "B04", tool_name="B05.search_tomato_knowledge")
    return ledger


def _judgement(weather_available=True):
    from B07.judgement import build_judgement

    bundle = _bundle()
    ledger = _ledger(weather_available=weather_available)
    return build_judgement(bundle, evidence_ledger=ledger)


def _context(weather_available=True):
    from B07.explain import format_judgement_context

    bundle = _bundle()
    bundle["evidence_ledger"] = _ledger(weather_available=weather_available)
    return format_judgement_context(bundle)


# --------------------------------------------------------------------------
# 1. 开关语义
# --------------------------------------------------------------------------

def test_switch_only_reacts_to_exactly_one(monkeypatch):
    from B07.weather import WEATHER_UNAVAILABLE_ENV, weather_unavailable_reason

    monkeypatch.delenv(WEATHER_UNAVAILABLE_ENV, raising=False)
    assert weather_unavailable_reason() is None

    monkeypatch.setenv(WEATHER_UNAVAILABLE_ENV, "0")
    assert weather_unavailable_reason() is None

    monkeypatch.setenv(WEATHER_UNAVAILABLE_ENV, "")
    assert weather_unavailable_reason() is None

    monkeypatch.setenv(WEATHER_UNAVAILABLE_ENV, "true")
    assert weather_unavailable_reason() is None

    monkeypatch.setenv(WEATHER_UNAVAILABLE_ENV, "1")
    reason = weather_unavailable_reason()
    assert reason and WEATHER_UNAVAILABLE_ENV in reason


def test_switch_off_leaves_everything_unchanged():
    """开关关闭 ⇒ 判定与限制字段与 S4 一字不差。"""
    j = _judgement(weather_available=True)
    assert "evidence_unavailable" not in j["limitations"]
    assert j["limitations"]["all_keys"] == [
        "simulated_data", "model_boundary", "time_anchor", "no_weather_no_kb"]
    assert j["external_conditions"]["status"] == "ok"
    assert j["external_conditions"]["forecast_time"] == "2026-10-25T04:00:00"


# --------------------------------------------------------------------------
# 2. 不可用是显式的
# --------------------------------------------------------------------------

def test_unavailable_tool_result_has_no_data_and_explicit_error():
    from B07.evidence_registry import make_tool_result

    tr = make_tool_result(source="B03/Open-Meteo", status="unavailable",
                          data=None, error="boom")
    assert tr["status"] == "unavailable"
    # 失败记录里**不许**残留任何结果载荷（字段名叫 data，不是 result）
    assert tr["data"] is None
    assert tr["error"] == "boom"
    # 不许把 error 写成空串
    with pytest.raises(Exception):
        make_tool_result(source="B03", status="unavailable",
                         data=None, error="")


def test_unavailable_evidence_keeps_provider_identity():
    """provider 身份保留（说明是谁不可用），但 forecast_time 置空。"""
    j = _judgement(weather_available=False)
    ext = j["external_conditions"]
    assert ext["status"] == "unavailable"
    assert ext["provider"] == "Offline-Fixture"
    assert ext["forecast_time"] is None
    assert ext["error"] and "B07_WEATHER_UNAVAILABLE" in ext["error"]
    assert ext["signal"] == "external_unavailable"


def test_unavailable_evidence_direction_is_undetermined():
    from B07.evidence_ledger import adopted_ledger_entries

    entries = adopted_ledger_entries(_ledger(weather_available=False))
    b03 = [e for e in entries if e["source_type"] == "B03"]
    assert len(b03) == 1
    assert b03[0]["status"] == "unavailable"
    assert b03[0]["direction"] == "undetermined"


# --------------------------------------------------------------------------
# 3. 不可用证据不进 supporting / opposing
# --------------------------------------------------------------------------

def test_unavailable_evidence_never_enters_supporting_or_opposing():
    div = _judgement(weather_available=False)["evidence_divergence"]
    for item in (div["supporting"] + div["opposing"]):
        assert item["source_type"] != "B03"
    assert div["has_conflict"] is False
    assert div["each_source"]["B03"] == "undetermined"


# --------------------------------------------------------------------------
# 4. ⑥ 块不许整块消失，⑦ 块要单列不可用证据
# --------------------------------------------------------------------------

def test_block_6_stays_present_and_says_unavailable():
    ctx = _context(weather_available=False)
    assert "【⑥ 外部天气条件】" in ctx
    assert "外部天气证据当前不可用" in ctx
    assert "Offline-Fixture" in ctx
    assert "B07_WEATHER_UNAVAILABLE" in ctx


def test_block_7_lists_unavailable_evidence_separately():
    ctx = _context(weather_available=False)
    assert "【⑦ 证据分歧】" in ctx
    assert "当前不可用的证据" in ctx
    assert "status：unavailable" in ctx


def test_no_fabricated_weather_numbers_in_degraded_context():
    """降级时上下文里不许出现天气数值（不许用静态文案伪装成功）。"""
    ctx = _context(weather_available=False)
    block6 = ctx.split("【⑥ 外部天气条件】", 1)[1].split("【⑦", 1)[0]
    for token in ("降水概率", "precipitation_probability", "气温预测",
                  "cloud_cover", "℃", "%"):
        assert token not in block6, f"⑥ 块里出现了不该有的天气内容：{token}"


# --------------------------------------------------------------------------
# 5. combined_attention 不再依赖外部方向
# --------------------------------------------------------------------------

def test_combined_attention_does_not_depend_on_external_when_unavailable():
    from B07.judgement import build_combined_attention

    bundle = _bundle()
    bundle["evidence_ledger"] = _ledger(weather_available=False)
    bundle["structured_judgement"] = _judgement(weather_available=False)
    out = build_combined_attention(bundle)
    assert out["value"] != "watch_external"
    assert out["value"] == "watch_internal"


def test_combined_attention_regression_when_weather_available():
    """天气正常时，combined_attention 与 S4 改动前**逐值相同**。

    S4 的场景：内部 WATCH + 外部 improving ⇒ 方向冲突 ⇒ conflict_unresolved。
    这一条锁住"降级改造没有改动既有规则"。
    """
    from B07.judgement import build_combined_attention

    bundle = _bundle()
    bundle["evidence_ledger"] = _ledger(weather_available=True)
    bundle["structured_judgement"] = _judgement(weather_available=True)
    # 存档里 external_conditions 是 provider 的原始返回（含 signal）
    bundle["external_conditions"] = _judgement(weather_available=True)[
        "external_conditions"]
    out = build_combined_attention(bundle)
    assert out["value"] == "conflict_unresolved"
    assert out["has_conflict"] is True
    assert out["external_signal"] == "external_improving"


# --------------------------------------------------------------------------
# 6. 限制字段只在真的有不可用证据时追加
# --------------------------------------------------------------------------

def test_limitations_gain_evidence_unavailable_only_when_unavailable():
    j = _judgement(weather_available=False)
    lim = j["limitations"]
    assert "evidence_unavailable" in lim["all_keys"]
    extra = lim["evidence_unavailable"]
    assert extra["key"] == "evidence_unavailable"
    assert extra["unavailable_sources"]
    src = extra["unavailable_sources"][0]
    assert src["status"] == "unavailable"
    assert "B07_WEATHER_UNAVAILABLE" in src["error"]
    # 基础四条一个都不能少
    for k in ("simulated_data", "model_boundary", "time_anchor",
              "no_weather_no_kb"):
        assert k in lim


# --------------------------------------------------------------------------
# 7. 提示词与固定问题指纹未变
# --------------------------------------------------------------------------

def test_prompt_fingerprints_unchanged():
    import hashlib

    from B07.explain import FIXED_QUESTION, SYSTEM_PROMPT

    assert hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest()[:12] \
        == SYSTEM_PROMPT_SHA12
    assert hashlib.sha256(FIXED_QUESTION.encode()).hexdigest()[:12] \
        == FIXED_QUESTION_SHA12


# --------------------------------------------------------------------------
# 8. 开关关闭时 provider 仍是正常路径
# --------------------------------------------------------------------------

def test_switch_off_provider_path_unchanged(monkeypatch):
    from B07.weather import WEATHER_UNAVAILABLE_ENV, weather_unavailable_reason

    monkeypatch.delenv(WEATHER_UNAVAILABLE_ENV, raising=False)
    assert weather_unavailable_reason() is None
    # 基线的离线 provider 依然给出 external_improving
    from B07.s4_offline import build_offline_cooling_context

    off = build_offline_cooling_context(ANCHOR)
    assert off["degraded"] is False
    assert off["context"]["signal"] == "external_improving"


def test_switch_on_offline_provider_returns_unavailable(monkeypatch):
    from B07.s4_offline import build_offline_cooling_context
    from B07.weather import WEATHER_UNAVAILABLE_ENV

    monkeypatch.setenv(WEATHER_UNAVAILABLE_ENV, "1")
    off = build_offline_cooling_context(ANCHOR)
    assert off["degraded"] is True
    ctx = off["context"]
    assert ctx["signal"] == "external_unavailable"
    assert ctx["available"] is False
    assert ctx["error"] and "B07_WEATHER_UNAVAILABLE" in ctx["error"]
    # provider 身份不变（对照才是"同一条证据的两种结果"）
    raw = off["raw_forecast"]
    assert raw["status"] == "unavailable"
    assert raw["data"] is None
    assert raw["provider"] == "Offline-Fixture"
    assert "B07_WEATHER_UNAVAILABLE" in raw["error"]


# --------------------------------------------------------------------------
# 9. 端到端（fake_llm，离线）：降级链路整条跑通
# --------------------------------------------------------------------------

def _install_fake_llm(monkeypatch, tmp_path):
    """把 LLM 换成假客户端（不联网、不花钱），并把存档目录指到临时目录。"""
    import B01.llm

    class _Msg:
        def __init__(self, content):
            self.content = content
            self.reasoning_content = ""

    class _Choice:
        def __init__(self, content):
            self.message = _Msg(content)

    class _Resp:
        def __init__(self, content, model):
            self.choices = [_Choice(content)]
            self.model = model
            self.usage = None

    class _Completions:
        def create(self, model=None, messages=None, **kw):
            return _Resp("假回答（离线测试用）", model or "fake-model")

    class _Chat:
        def __init__(self):
            self.completions = _Completions()

    class _Client:
        def __init__(self):
            self.chat = _Chat()

    monkeypatch.setattr(B01.llm, "get_client", lambda: _Client())
    monkeypatch.setenv("LLM_MODEL", "fake-model")

    # 存档目录重定向到 pytest 的临时目录（不许污染真实存档）
    import B07.runs as runs_mod
    tmp = tmp_path / "runs"
    tmp.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(runs_mod, "RUNS_DIR", tmp)
    monkeypatch.setattr(runs_mod, "INDEX_PATH", tmp / "index.json")


def test_end_to_end_degraded_run_is_explicit(monkeypatch, tmp_path):
    """降级端到端：跑完一条真实链路，失败必须**显式**暴露在存档里。"""
    from B07.s5 import run_variant
    from B07.weather import WEATHER_UNAVAILABLE_ENV

    _install_fake_llm(monkeypatch, tmp_path)
    monkeypatch.setenv(WEATHER_UNAVAILABLE_ENV, "1")

    v = run_variant(degraded=True)
    a = v["archive"]
    assert a.get("status") == "success"

    # ⑥ 块来源：unavailable，provider 保留，forecast_time 空
    ext = (a.get("structured_judgement") or {}).get("external_conditions") or {}
    assert ext.get("status") == "unavailable"
    assert ext.get("provider") == "Offline-Fixture"
    assert ext.get("forecast_time") is None
    assert ext.get("error")

    # 工具台账：天气那条 status=unavailable、error 非空
    calls = (a.get("evidence_ledger") or {}).get("tool_calls", {}).get("records") or []
    weather = [c for c in calls if c.get("tool_name") == "B05.get_weather_forecast"]
    assert weather and weather[0]["status"] == "unavailable"
    assert weather[0]["error"]

    # ⑦ 块：不可用证据单列，且不进 supporting / opposing
    ctx = a.get("llm_context") or ""
    assert "【⑦ 证据分歧】" in ctx
    assert "当前不可用的证据" in ctx
    div = (a.get("structured_judgement") or {}).get("evidence_divergence") or {}
    assert all(i.get("source_type") != "B03"
               for i in (div.get("supporting") or []) + (div.get("opposing") or []))


def test_end_to_end_does_not_pollute_real_runs_dir(monkeypatch, tmp_path):
    """fake_llm 端到端**不许**往真实存档目录里写东西。"""
    import hashlib

    from B07.runs import RUNS_DIR

    real = RUNS_DIR
    before = sorted(p.name for p in real.iterdir()) if real.exists() else []

    _install_fake_llm(monkeypatch, tmp_path)
    from B07.s5 import run_variant
    from B07.weather import WEATHER_UNAVAILABLE_ENV

    monkeypatch.setenv(WEATHER_UNAVAILABLE_ENV, "1")
    run_variant(degraded=True)

    after = sorted(p.name for p in real.iterdir()) if real.exists() else []
    assert before == after
