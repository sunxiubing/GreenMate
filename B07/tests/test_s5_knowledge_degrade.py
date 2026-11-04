"""B07 S5-2 知识降级的不变量测试（全部离线，不联网、不调真实 LLM）

锁住的是这几条底线：

  1. 开关只有一个：B07_KNOWLEDGE_UNAVAILABLE，只认 "1"；
     不设 / 其它值 ⇒ 检索照旧（基线路径零影响）
  2. 不可用是**显式**的：status="unavailable"、result=None、error 非空，
     且 error 以固定前缀开头，指明是开关导致的
  3. 检索失败 ≠ 命中 0 条：摘要写「知识证据不可用」，
     undetermined_reason 点明「知识证据当前不可用」
  4. 不可用证据 direction=undetermined，不进 supporting / opposing
  5. ⑦ 块把不可用证据单列，限制字段追加 evidence_unavailable
  6. 「知识证据只支持『需要关注』」这条限定只在检索成功时出现
  7. 证明二的 F-1~F-4 能对 B04 跑（自动识别），且不破坏 B03 路径

硬约束：
  - 不改已有测试；本文件只新增断言
  - 知识用夹具 / 本地 TF-IDF，不联网

跑法：
  ./venv/Scripts/python.exe -m pytest B07/tests/test_s5_knowledge_degrade.py -v
"""

import pytest

ANCHOR = "2026-10-21T09:30:00"
ERROR_PREFIX = "knowledge retrieval unavailable (B07_KNOWLEDGE_UNAVAILABLE=1):"


# --------------------------------------------------------------------------
# 1. 开关语义
# --------------------------------------------------------------------------

def test_switch_only_reacts_to_exactly_one(monkeypatch):
    from B07.knowledge import (KNOWLEDGE_UNAVAILABLE_ENV,
                               knowledge_unavailable_reason)

    monkeypatch.delenv(KNOWLEDGE_UNAVAILABLE_ENV, raising=False)
    assert knowledge_unavailable_reason() is None

    monkeypatch.setenv(KNOWLEDGE_UNAVAILABLE_ENV, "0")
    assert knowledge_unavailable_reason() is None

    monkeypatch.setenv(KNOWLEDGE_UNAVAILABLE_ENV, "")
    assert knowledge_unavailable_reason() is None

    monkeypatch.setenv(KNOWLEDGE_UNAVAILABLE_ENV, "true")
    assert knowledge_unavailable_reason() is None

    monkeypatch.setenv(KNOWLEDGE_UNAVAILABLE_ENV, "1")
    reason = knowledge_unavailable_reason()
    assert reason and KNOWLEDGE_UNAVAILABLE_ENV in reason


def test_switch_off_still_retrieves(monkeypatch):
    """开关关闭 ⇒ 本地检索照旧，默认 query 仍命中 CH-003-02。"""
    from B07.knowledge import (DEFAULT_QUERY, KNOWLEDGE_UNAVAILABLE_ENV,
                               search_knowledge)

    monkeypatch.delenv(KNOWLEDGE_UNAVAILABLE_ENV, raising=False)
    ev = search_knowledge(DEFAULT_QUERY)
    assert ev["status"] == "ok"
    assert ev["result"]["citations"]
    assert ev["ext"]["citation"]["chunk_id"] == "CH-003-02"


# --------------------------------------------------------------------------
# 2. 不可用是显式的
# --------------------------------------------------------------------------

def test_switch_on_returns_explicit_unavailable_evidence(monkeypatch):
    from B07.knowledge import (DEFAULT_QUERY, KNOWLEDGE_UNAVAILABLE_ENV,
                               search_knowledge)

    monkeypatch.setenv(KNOWLEDGE_UNAVAILABLE_ENV, "1")
    ev = search_knowledge(DEFAULT_QUERY)

    assert ev["status"] == "unavailable"
    assert ev["source_type"] == "b04_knowledge"
    # 失败记录里不许残留任何检索结果
    assert ev["result"] is None
    assert ev["error"].startswith(ERROR_PREFIX)
    # 查询问题本身要留下，证明"这次问的是什么"
    assert ev["ext"]["question"] == DEFAULT_QUERY
    assert ev["ext"]["citation"] is None
    assert ev["ext"]["retrieved"] == []
    assert ev["ext"]["has_relevant"] is False


def test_unavailable_evidence_direction_is_undetermined(monkeypatch):
    from B07.evidence_ledger import EvidenceLedger, adopted_ledger_entries
    from B07.evidence_registry import make_tool_result
    from B07.knowledge import (DEFAULT_QUERY, KNOWLEDGE_UNAVAILABLE_ENV,
                               search_knowledge)

    monkeypatch.setenv(KNOWLEDGE_UNAVAILABLE_ENV, "1")
    kb = search_knowledge(DEFAULT_QUERY)

    led = EvidenceLedger()
    led.record_call("B05.search_tomato_knowledge", {"query": DEFAULT_QUERY},
                    status=kb["status"], error=kb["error"])
    led.add_evidence(
        make_tool_result(source=kb["source"], status=kb["status"],
                         data=kb["result"], error=kb["error"]),
        "B04", ext=kb.get("ext") or {},
        tool_name="B05.search_tomato_knowledge")

    b04 = [e for e in adopted_ledger_entries(led)
           if e["source_type"] == "B04"]
    assert len(b04) == 1
    assert b04[0]["status"] == "unavailable"
    assert b04[0]["direction"] == "undetermined"


# --------------------------------------------------------------------------
# 3. 检索失败 ≠ 命中 0 条
# --------------------------------------------------------------------------

def test_summary_and_reason_say_unavailable_not_zero_hits():
    from B07.evidence_ledger import summarize, undetermined_reason

    entry = {
        "status": "unavailable", "source_type": "B04",
        "result": None, "error": ERROR_PREFIX + " 模拟故障",
    }
    summary = summarize(entry)
    reason = undetermined_reason(entry)
    assert "知识证据不可用" in summary
    assert "命中 0 条" not in summary
    assert "知识证据当前不可用" in reason
    assert "不改变关注判断" in reason


def test_only_supports_attention_limitation_is_gated_on_ok():
    """检索没成时，不许再挂「知识证据只支持『需要关注』」这条限定。"""
    from B07.evidence_ledger import limitations_of

    ok_entry = {
        "status": "ok", "source_type": "B04",
        "result": {"citations": [], "query": "q"},
        "ext": {},
    }
    bad_entry = {
        "status": "unavailable", "source_type": "B04",
        "result": None, "error": ERROR_PREFIX + " 模拟故障",
        "ext": {"question": "q", "citation": None},
    }
    ok_summaries = [x["summary"] for x in limitations_of(ok_entry)]
    bad_summaries = [x["summary"] for x in limitations_of(bad_entry)]
    assert "知识证据只支持『需要关注』" in ok_summaries
    assert "知识证据只支持『需要关注』" not in bad_summaries
    assert any("证据源不可用" in s for s in bad_summaries)


# --------------------------------------------------------------------------
# 4. 端到端（fake_llm，离线）：知识降级链路整条跑通
# --------------------------------------------------------------------------

def _install_fake_llm(monkeypatch, tmp_path):
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

    import B07.runs as runs_mod
    tmp = tmp_path / "runs"
    tmp.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(runs_mod, "RUNS_DIR", tmp)
    monkeypatch.setattr(runs_mod, "INDEX_PATH", tmp / "index.json")


def test_end_to_end_knowledge_degraded_run_is_explicit(monkeypatch, tmp_path):
    from B07.knowledge import KNOWLEDGE_UNAVAILABLE_ENV
    from B07.s5 import run_variant

    _install_fake_llm(monkeypatch, tmp_path)
    monkeypatch.setenv(KNOWLEDGE_UNAVAILABLE_ENV, "1")

    v = run_variant(degraded=False)          # 只降知识，天气保持正常
    a = v["archive"]
    assert a.get("status") == "success"

    # 工具台账：知识那条 status=unavailable、error 非空
    calls = (a.get("evidence_ledger") or {}).get(
        "tool_calls", {}).get("records") or []
    kb_calls = [c for c in calls
                if c.get("tool_name") == "B05.search_tomato_knowledge"]
    assert kb_calls and kb_calls[0]["status"] == "unavailable"
    assert kb_calls[0]["error"].startswith(ERROR_PREFIX)

    # 证据：B04 unavailable + undetermined，result 为空
    entries = (a.get("evidence_ledger") or {}).get("entries") or []
    b04 = [e for e in entries if e.get("source_type") == "B04"]
    assert len(b04) == 1
    assert b04[0]["status"] == "unavailable"
    assert b04[0]["direction"] == "undetermined"
    assert b04[0]["result"] is None
    assert b04[0]["error"].startswith(ERROR_PREFIX)

    # ⑦ 块：不可用证据单列；B04 不进 supporting / opposing
    div = (a.get("structured_judgement") or {}).get(
        "evidence_divergence") or {}
    assert all(i.get("source_type") != "B04"
               for i in (div.get("supporting") or [])
               + (div.get("opposing") or []))
    b04_und = [i for i in (div.get("undetermined") or [])
               if i.get("source_type") == "B04"]
    assert b04_und
    assert any("知识证据不可用" in i.get("summary", "") for i in b04_und)
    assert any("知识证据当前不可用" in i.get("reason", "") for i in b04_und)

    ctx = a.get("llm_context") or ""
    assert "当前不可用的证据" in ctx

    # 限制字段：evidence_unavailable 记的是知识这条
    lim = (a.get("structured_judgement") or {}).get("limitations") or {}
    assert "evidence_unavailable" in (lim.get("all_keys") or [])
    srcs = lim["evidence_unavailable"]["unavailable_sources"]
    assert any(s.get("source_type") == "B04" for s in srcs)

    # 其他证据不受影响；合成关注等级仍能算出来
    for st in ("B02", "B03", "B06"):
        e = [x for x in entries if x.get("source_type") == st][0]
        assert e["status"] == "ok"
    assert (a.get("combined_attention") or {}).get("value") is not None


# --------------------------------------------------------------------------
# 5. 证明二：F-1~F-4 能对 B04 跑（自动识别），B03 路径不受影响
# --------------------------------------------------------------------------

def _archive(*, source_type, source_status, tool_name,
             error=None, ca="conflict_unresolved", conflict=True,
             explanation="内部稳定判据显示温度波动超过阈值。"):
    entries = [
        {"source_type": "B02", "status": "ok", "direction": "supporting"},
        {"source_type": "B06", "status": "ok", "direction": "supporting"},
        {"source_type": "B03", "status": "ok", "direction": "opposing"},
        {"source_type": "B04", "status": "ok", "direction": "supporting"},
    ]
    for e in entries:
        if e["source_type"] == source_type:
            e["status"] = source_status
            e["direction"] = ("undetermined" if source_status != "ok"
                              else e["direction"])
            e["error"] = error
    return {
        "run_id": "fake",
        "llm_explanation": explanation,
        "combined_attention": {"value": ca},
        "structured_judgement": {
            "recent_trend": {"stability_verdict": "unstable"},
            "evidence_divergence": {"has_conflict": conflict},
        },
        "evidence_ledger": {
            "entries": entries,
            "tool_calls": {"records": [{
                "tool_name": tool_name,
                "status": source_status,
                "error": error,
            }]},
        },
    }


def _run_suite(monkeypatch, fail_arch, base_arch):
    import B07.b08_trace as b08

    monkeypatch.setattr(
        b08, "_arch",
        lambda rid: fail_arch if rid == "run_fail" else base_arch)
    s = b08.failure_suite("run_fail", "run_base")
    s["verdict"] = ("PASS" if all(s[k]["passed"]
                                  for k in ("F-1", "F-2", "F-3", "F-4"))
                    and s["counter_evidence"]["passed"] else "FAIL")
    return s


def test_failure_suite_auto_detects_b04_and_passes(monkeypatch):
    fail = _archive(
        source_type="B04", source_status="unavailable",
        tool_name="B05.search_tomato_knowledge",
        error=ERROR_PREFIX + " 模拟知识库检索故障",
        explanation=("知识证据不可用，本次不参与判定。"
                     "内部稳定判据显示温度波动超过阈值。"))
    base = _archive(source_type="B04", source_status="ok",
                    tool_name="B05.search_tomato_knowledge")
    s = _run_suite(monkeypatch, fail, base)
    assert s["source_type"] == "B04"
    assert s["F-1"]["passed"] and s["F-2"]["passed"]
    assert s["F-3"]["passed"] and s["F-4"]["passed"]
    assert s["counter_evidence"]["passed"]
    assert s["verdict"] == "PASS"


def test_failure_suite_still_works_for_b03(monkeypatch):
    weather_error = ("weather provider unavailable "
                     "(B07_WEATHER_UNAVAILABLE=1): 模拟天气 API 故障")
    fail = _archive(
        source_type="B03", source_status="unavailable",
        tool_name="B05.get_weather_forecast", error=weather_error,
        explanation="外部天气不可用，本次不参与判定。内部阈值判据仍然成立。")
    base = _archive(source_type="B03", source_status="ok",
                    tool_name="B05.get_weather_forecast")
    s = _run_suite(monkeypatch, fail, base)
    assert s["source_type"] == "B03"
    assert s["verdict"] == "PASS"


def test_knowledge_fact_detector_flags_fabricated_citation():
    from B07.b08_trace import find_knowledge_facts

    assert find_knowledge_facts("知识库显示 CH-003-02 支持该结论。")
    # 免责句不算编造
    assert not [h for h in find_knowledge_facts(
        "知识检索不可用，无法引用 CH-003-02。") if h["counted"]]
