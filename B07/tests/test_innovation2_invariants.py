"""B07 创新点 2 的不变量测试（离线，不联网、不调 LLM）

覆盖的东西：
  1. 溯源器输出**严格**符合规格写死的 JSON 形状
  2. 挑句规则：必须含判断/建议类动词；限制陈述走另一条路
  3. 数值提取要剥掉版本号与时间戳（否则会产生假数值）
  4. 工具调用认领：唯一最高才认领，并列则不认领（不硬凑）
  5. V3 红线：谈天气必须有天气工具调用记录（用**构造**的 bundle 测，
     正例/反例都测）
  6. V6 台账自洽（用真实 run）
  7. 溯源过程**不写** B07/runs/，**不调 LLM**
  8. 报告必须含规定的 9 个章节与 L1–L5

跑法：`python -m pytest B07/tests -q`
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from B07.innovation2 import (
    CLAIM_VERBS,
    DEFAULT_RUN,
    DETAIL_JSON_PATH,
    LIMITATION_CUES,
    REPORT_PATH,
    TRACE_JSON_PATH,
    build_report,
    build_terminal_sections,
    classify_sentence,
    match_tool_call,
    numbers_in,
    pick_claims,
    pick_limitation_statements,
    render_terminal,
    run_checks,
    runs_with_ledger,
    scan_all_runs,
    split_sentences,
    to_spec_format,
    trace_run,
)
from B07.runs import RUNS_DIR, load_run

SPEC_TOP_KEYS = {"source_run_id", "source_run_path", "claims", "tool_calls",
                 "checks"}
SPEC_CLAIM_KEYS = {"claim_text", "claim_index", "evidence", "missing_roles",
                   "chain_ok"}
SPEC_EVIDENCE_KEYS = {"role", "source", "time", "status", "result_summary",
                      "basis", "tool_call", "adopted", "ledger_path"}
SPEC_CHECKS = {"V1", "V2", "V3", "V4", "V5", "V6"}


# --------------------------------------------------------------------------
# 夹具
# --------------------------------------------------------------------------

@pytest.fixture(scope="module")
def trace_082():
    try:
        return trace_run(DEFAULT_RUN)
    except Exception:            # pragma: no cover - 存档不在就跳过
        pytest.skip(f"{DEFAULT_RUN} 存档不可用")


@pytest.fixture(scope="module")
def bundle_082():
    try:
        return load_run(DEFAULT_RUN)
    except Exception:            # pragma: no cover
        pytest.skip(f"{DEFAULT_RUN} 存档不可用")


# --------------------------------------------------------------------------
# 1. 输出格式严格符合规格
# --------------------------------------------------------------------------

def test_spec_format_has_exactly_the_specified_keys(trace_082):
    spec = to_spec_format(trace_082)
    assert set(spec) == SPEC_TOP_KEYS
    for c in spec["claims"]:
        assert set(c) == SPEC_CLAIM_KEYS
        for e in c["evidence"]:
            assert set(e) == SPEC_EVIDENCE_KEYS
            assert set(e["tool_call"]) == {"tool_name", "arguments", "status"}
    for t in spec["tool_calls"]:
        assert set(t) == {"tool_name", "status", "arguments", "adopted"}
    assert set(spec["checks"]) == SPEC_CHECKS
    assert all(isinstance(v, bool) for v in spec["checks"].values())


def test_trace_json_artifact_matches_spec(trace_082):
    """落盘的 trace 文件里，主 run 的形状必须与规格一致。"""
    if not TRACE_JSON_PATH.exists():
        pytest.skip("尚未生成 innovation2_trace.json")
    blob = json.loads(TRACE_JSON_PATH.read_text(encoding="utf-8"))
    assert DEFAULT_RUN in blob["traces"]
    spec = blob["traces"][DEFAULT_RUN]
    assert set(spec) == SPEC_TOP_KEYS
    assert spec["source_run_id"] == DEFAULT_RUN
    assert spec["source_run_path"] == f"B07/runs/{DEFAULT_RUN}/run.json"


def test_ledger_paths_point_to_real_indices(trace_082):
    """ledger_path 里的下标必须真的落在 evidence_ledger.entries 里。"""
    ledger = load_run(trace_082["source_run_id"])["evidence_ledger"]
    n = len(ledger["entries"])
    for c in trace_082["claims"]:
        for e in c["evidence"]:
            idx = int(e["ledger_path"].split("[")[1].split("]")[0])
            assert 0 <= idx < n
            entry = ledger["entries"][idx]
            assert entry["source_type"] == e["source_type"]
            assert entry["source"] == e["source"]
            assert entry["status"] == e["status"]


def test_every_evidence_has_a_real_tool_call(trace_082):
    """V2：被引用到的证据都必须接到一次真实发生的工具调用上。"""
    for c in trace_082["claims"]:
        for e in c["evidence"]:
            assert e["tool_call"]["tool_name"], \
                f"{e['evidence_id']} 没找到工具调用"
            assert e["tool_call"]["status"] in ("ok", "unavailable", "error")
            assert e["tool_call_derivation"], \
                "工具调用的认领依据必须写出来"


def test_claims_are_not_empty(trace_082):
    assert 1 <= len(trace_082["claims"]) <= 3


# --------------------------------------------------------------------------
# 2. 挑句规则
# --------------------------------------------------------------------------

def test_picked_claims_all_contain_a_claim_verb(trace_082):
    for c in trace_082["claims"]:
        assert any(v in c["claim_text"] for v in CLAIM_VERBS)


def test_pure_limitation_sentence_is_not_a_claim(trace_082):
    """纯限制陈述（只有动词、没有证据线索）不能混进结论句。"""
    for c in trace_082["claims"]:
        assert "模拟数据" not in c["claim_text"] or "波动" in c["claim_text"] \
            or "预测" in c["claim_text"]


def test_classify_sentence_is_cue_based_only():
    """限制分类只看限制性短语，不做原文 4-gram 重叠。"""
    limits = {"all_keys": list(LIMITATION_CUES)}
    # 命中限制性短语 → 是限制陈述
    r = classify_sentence("本次数据为模拟数据，不可外推。", limits)
    assert r["is_limitation"] is True
    assert r["cue_hits"]
    # 只跟 limitation 原文里的数字重合 → 不是限制陈述（4-gram 回退已废弃）
    r2 = classify_sentence("温度波动4.4℃，超过阈值2.0℃。", limits)
    assert r2["is_limitation"] is False
    assert set(r2) == {"is_limitation", "cue_hits"}


def test_every_limitation_key_has_cues():
    """5 个 limitation key 都必须有对应的限制性短语，否则会漏判。"""
    for key in ("simulated_data", "model_boundary", "time_anchor",
                "no_weather_no_kb", "evidence_unavailable"):
        assert key in LIMITATION_CUES
        assert LIMITATION_CUES[key]


def test_evidence_unavailable_cue_catches_unavailable_statement():
    limits = {"all_keys": list(LIMITATION_CUES)}
    r = classify_sentence("天气工具当前不可用，未发起真实调用。", limits)
    assert r["is_limitation"] is True
    assert "evidence_unavailable" in r["cue_hits"]


def test_limitation_statements_trace_to_limitation_fields(trace_082):
    """限制陈述要挂到 structured_judgement.limitations.<key> 上。"""
    stmts = trace_082["limitation_statements"]
    assert stmts, "本 run 的回答里应该有至少一条限制陈述"
    for s in stmts:
        assert s["limitation_path"]
        for p in s["limitation_path"]:
            key = p.rsplit(".", 1)[-1]
            assert p.startswith("structured_judgement.limitations.")
            assert key


def test_split_sentences_keeps_commas_inside_a_sentence():
    s = split_sentences("温度 28.6℃，湿度 66.9%。环境不稳定，建议关注。")
    assert s == ["温度 28.6℃，湿度 66.9%。", "环境不稳定，建议关注。"]


def test_pick_claims_requires_evidence_signal():
    """没有证据线索的句子即使含动词也不挑。"""
    entries = []
    picked = pick_claims("需要注意的是，本次所有环境数据均为模拟数据。", entries)
    assert picked == []


def test_manual_claim_bypasses_the_picking_rules():
    """教师人工指定的句子**无条件**进入溯源（哪怕它没有判断/建议动词）。"""
    answer = ("根据当前的监测数据，温度为28.6℃。"
              "具体表现为：温度波动4.4℃，超过阈值2.0℃。")
    picked = pick_claims(answer, [], manual=["具体表现为：温度波动4.4℃，超过阈值2.0℃。"])
    assert len(picked) == 1
    assert picked[0]["manual"] is True
    assert "教师人工指定" in picked[0]["selection_rule"]


def test_manual_claim_does_not_displace_rule_picked_ones():
    answer = ("句子甲，需要提高关注程度，温度波动明显。"
              "句子乙，需要关注湿度变化。")
    picked = pick_claims(answer, [], max_claims=1,
                         manual=["句子乙，需要关注湿度变化。"])
    assert len(picked) == 2
    assert any(p["manual"] for p in picked)


def test_manual_claim_end_to_end():
    """人工指定 run_082 的第 3 句（细节数字句），必须能追到 B02 证据。"""
    from B07.runs import load_run as _load

    answer = _load(DEFAULT_RUN).get("llm_explanation") or ""
    sents = split_sentences(answer)
    tr = trace_run(DEFAULT_RUN, manual_claims=[sents[3]])
    manual = [c for c in tr["claims"] if c["claim_index"] == 3]
    assert manual, "人工指定的句子没进候选"
    ev = manual[0]["evidence"]
    assert ev
    assert ev[0]["role"] == "history"
    assert ev[0]["number_hits"], "细节数字句应当靠数值命中"
    assert ev[0]["match_strength"] == "strong"
    assert ev[0]["tool_call"]["tool_name"] == "B02.get_environment_history"


# --------------------------------------------------------------------------
# 3. 数值提取
# --------------------------------------------------------------------------

def test_numbers_in_strips_versions_and_timestamps():
    assert "20261020" not in numbers_in("版本b06-20261020-v1")
    assert "06" not in numbers_in("版本b06-20261020-v1")
    assert "2026" not in numbers_in("2026-10-21T09:30:00 时刻")
    assert "1.0" in numbers_in("置信度为1.0")
    assert "4.4" in numbers_in("温度波动 4.4℃")


# --------------------------------------------------------------------------
# 4. 工具调用认领
# --------------------------------------------------------------------------

def test_match_tool_call_requires_unique_winner():
    entry = {"result": {"location": "河北固安"}, "ext": {}}
    records = [
        {"tool_name": "A", "arguments": {"location": "河北固安"},
         "status": "ok", "adopted": True},
        {"tool_name": "B", "arguments": {"location": "河北固安"},
         "status": "ok", "adopted": True},
    ]
    assert match_tool_call(entry, records) is None      # 并列 → 不认领


def test_match_tool_call_picks_the_only_match():
    entry = {"result": {"location": "河北固安"}, "ext": {}}
    records = [
        {"tool_name": "B05.get_weather_forecast",
         "arguments": {"location": "河北固安"}, "status": "ok",
         "adopted": True},
        {"tool_name": "B05.get_current_environment",
         "arguments": {"location": "A区-3号温室"}, "status": "ok",
         "adopted": False},
    ]
    got = match_tool_call(entry, records)
    assert got["tool_name"] == "B05.get_weather_forecast"
    assert got["matched_arguments"] == ["location=河北固安"]


def test_match_tool_call_returns_none_without_any_hit():
    entry = {"result": {"x": 1}, "ext": {}}
    records = [{"tool_name": "A", "arguments": {"q": "完全对不上的字符串"},
                "status": "ok", "adopted": True}]
    assert match_tool_call(entry, records) is None


# --------------------------------------------------------------------------
# 5. V3 / V4 的红线判定（用构造的 bundle）
# --------------------------------------------------------------------------

def _fake_bundle(answer: str, tool_calls: list[dict],
                 entries: list[dict] | None = None,
                 adoption: dict | None = None) -> dict:
    entries = entries if entries is not None else [{
        "evidence_id": "EV-X-01", "source_type": "B02",
        "source": "B02/x", "time": "2026-10-21T09:30:00", "status": "ok",
        "result": {}, "error": None, "ext": {}, "adopted": True,
        "direction": "supporting",
    }]
    calls = [{"tool_name": t["tool_name"], "arguments": t.get("arguments", {}),
              "status": t.get("status", "ok"), "adopted": t.get("adopted", True),
              "error": t.get("error")} for t in tool_calls]
    return {
        "llm_explanation": answer,
        "evidence_ledger": {
            "entries": entries,
            "tool_calls": {"records": calls},
            "adoption": adoption or {
                "total_calls": len(calls),
                "adopted_calls": sum(1 for c in calls if c["adopted"]),
                "not_adopted_calls": [c for c in calls if not c["adopted"]],
                "text": "",
            },
        },
    }


def test_v3_red_line_fails_when_weather_claimed_without_call():
    """回答里说了天气，但没有任何天气工具调用 → 判不通过。"""
    b = _fake_bundle("根据天气信息，未来将降温，建议关注。",
                     [{"tool_name": "B02.get_environment_history"}])
    checks = run_checks(b, [], b["evidence_ledger"]["tool_calls"]["records"])
    assert checks["V3"] is False
    assert checks["details"]["V3"]["triggered"] is True
    assert checks["details"]["V3"]["weather_tool_calls"] == []


def test_v3_red_line_passes_when_weather_tool_was_called():
    b = _fake_bundle(
        "根据天气信息，未来将降温，建议关注。",
        [{"tool_name": "B05.get_weather_forecast",
          "arguments": {"location": "河北固安"}}])
    checks = run_checks(b, [], b["evidence_ledger"]["tool_calls"]["records"])
    assert checks["V3"] is True
    assert len(checks["details"]["V3"]["weather_tool_calls"]) == 1


def test_v4_fails_when_knowledge_claimed_without_retrieval():
    b = _fake_bundle("依据知识库条目 CH-003-02，建议关注。",
                     [{"tool_name": "B02.get_environment_history"}])
    checks = run_checks(b, [], b["evidence_ledger"]["tool_calls"]["records"])
    assert checks["V4"] is False


def test_v4_passes_when_kb_retrieval_recorded():
    b = _fake_bundle(
        "依据知识库条目 CH-003-02，建议关注。",
        [{"tool_name": "B05.search_tomato_knowledge",
          "arguments": {"query": "过期数据"}}])
    checks = run_checks(b, [], b["evidence_ledger"]["tool_calls"]["records"])
    assert checks["V4"] is True


def test_v3_not_triggered_when_answer_never_mentions_weather():
    b = _fake_bundle("建议关注温湿度变化。",
                     [{"tool_name": "B02.get_environment_history"}])
    checks = run_checks(b, [], b["evidence_ledger"]["tool_calls"]["records"])
    assert checks["details"]["V3"]["triggered"] is False
    assert checks["V3"] is True


# --------------------------------------------------------------------------
# 6. V6 台账自洽（真实 run）
# --------------------------------------------------------------------------

def test_v6_self_consistency_on_real_run(trace_082):
    v6 = trace_082["checks"]["details"]["V6"]
    assert v6["passed"] is True
    assert v6["adopted_calls"] + v6["not_adopted_calls_count"] == \
        v6["total_calls"]
    assert v6["adopted_calls"] == v6["adopted_entries_count"]


def test_v1_and_v2_hold_on_real_run(trace_082):
    assert trace_082["checks"]["V1"] is True
    assert trace_082["checks"]["V2"] is True


def test_v1_marks_applicable_and_notes_vacuous_truth(trace_082):
    """V1 必须显式区分"有检验对象"与"空真"，不许拿空真充数。"""
    v1 = trace_082["checks"]["details"]["V1"]
    assert v1["applicable"] is True
    assert v1["claims_selected"] == len(trace_082["claims"])
    assert v1["note"] == ""


def test_v1_is_vacuous_when_no_claim_selected():
    """挑不出结论句时 V1 仍为 True，但必须标 applicable=False 并写明原因。"""
    b = _fake_bundle("本次未产生任何结论。", [])
    checks = run_checks(b, [], [])
    v1 = checks["details"]["V1"]
    assert v1["passed"] is True
    assert v1["applicable"] is False
    assert "空真" in v1["note"]


def test_every_tool_call_record_is_listed(trace_082):
    """tool_calls 里必须出现台账记录的每一次调用（含未被采用的）。"""
    ledger = load_run(trace_082["source_run_id"])["evidence_ledger"]
    assert len(trace_082["tool_calls"]) == \
        len(ledger["tool_calls"]["records"])
    names = {t["tool_name"] for t in trace_082["tool_calls"]}
    assert names == {r["tool_name"] for r in ledger["tool_calls"]["records"]}


# --------------------------------------------------------------------------
# 7. 不写 runs、不调 LLM
# --------------------------------------------------------------------------

def test_tracing_does_not_write_into_runs_dir(trace_082):
    before = sorted(p.name for p in RUNS_DIR.glob("run_*"))
    trace_run(DEFAULT_RUN)
    after = sorted(p.name for p in RUNS_DIR.glob("run_*"))
    assert before == after


def test_tracing_makes_no_llm_call(monkeypatch, trace_082):
    """把 LLM 客户端和网络都堵死，溯源仍必须跑通（说明它没调 LLM）。"""
    import socket
    import sys

    import B01.llm as b01_llm

    def boom(*a, **kw):
        raise AssertionError("溯源器不允许调用 LLM")

    monkeypatch.setattr(b01_llm, "get_client", boom)

    real_socket = socket.socket

    class NoNet(real_socket):        # type: ignore[misc]
        def connect(self, *a, **kw):
            raise AssertionError("溯源器不允许联网")

        def connect_ex(self, *a, **kw):
            raise AssertionError("溯源器不允许联网")

    monkeypatch.setattr(socket, "socket", NoNet)
    trace_run(DEFAULT_RUN)
    assert "B01" in sys.modules or True      # 仅记录：B01 已被 import 过


# --------------------------------------------------------------------------
# 8. 报告
# --------------------------------------------------------------------------

def test_report_contains_required_sections():
    if not REPORT_PATH.exists():
        pytest.skip("尚未生成 innovation2_report.md")
    md = REPORT_PATH.read_text(encoding="utf-8")
    for must in ("## 0 一句话结论", "## 1 四类信息", "## 2 溯源链实录",
                 "## 3 第四步", "## 4 第五步", "## 5 黑盒对照",
                 "## 6 文档五步逐条自评", "## 7 Limitations", "## 8 复现命令"):
        assert must in md, f"报告缺少章节：{must}"
    for lim in ("**L1**", "**L2**", "**L3**", "**L4**", "**L5**"):
        assert lim in md, f"报告缺少 Limitation {lim}"


def test_report_blackbox_section_reads_the_existing_contrast_file():
    """黑盒对照的数据必须来自 s4_3_contrast.json，不许手抄。"""
    contrast = Path(__file__).resolve().parents[1] / "artifacts" / \
        "s4_3_contrast.json"
    if not contrast.exists() or not REPORT_PATH.exists():
        pytest.skip("缺对照数据或报告")
    md = REPORT_PATH.read_text(encoding="utf-8")
    assert "s4_3_contrast.json" in md
    data = json.loads(contrast.read_text(encoding="utf-8"))
    groups = data.get("groups") or {}
    for g in groups.values():
        summary = g.get("summary") or {}
        for key in ("claims_total", "attribution_rate_text",
                    "unsupported_rate_text", "no_claim_rate_text"):
            val = summary.get(key)
            if val is None:
                continue
            assert str(val) in md, f"对照数值 {key}={val} 没出现在报告里"


def test_build_report_is_reproducible(trace_082):
    """同样的输入必须生成同样的报告（不掺随机、不掺时间戳）。"""
    traces = {DEFAULT_RUN: trace_082}
    a = build_report(traces)
    b = build_report(traces)
    assert a == b


def test_detail_artifact_exists_and_has_details():
    if not DETAIL_JSON_PATH.exists():
        pytest.skip("尚未生成 innovation2_trace_detail.json")
    blob = json.loads(DETAIL_JSON_PATH.read_text(encoding="utf-8"))
    for rid, t in blob["traces"].items():
        assert "details" in t["checks"]
        assert set(t["checks"]["details"]) == {"V1", "V2", "V3", "V4", "V5", "V6"}


# --------------------------------------------------------------------------
# 9. 系统级扫描（全库所有带台账的 run）
# --------------------------------------------------------------------------

@pytest.fixture(scope="module")
def system_scan():
    return scan_all_runs()


def test_system_scan_covers_every_ledger_run(system_scan):
    ledger_runs = runs_with_ledger()
    assert ledger_runs, "库里应当有带 evidence_ledger 的 run"
    assert system_scan["runs_total"] == len(ledger_runs)
    assert system_scan["runs_traced"] == len(ledger_runs)
    assert system_scan["errors"] == {}, "扫描不得有未暴露的异常"


def test_system_scan_has_no_redline_or_consistency_violation(system_scan):
    """全库范围内 V3/V4 红线、V6 自洽必须零违反。"""
    for key in ("V3", "V4", "V6"):
        c = system_scan["checks"][key]
        assert c["passed"] == c["total"], f"{key} 有失败 run：{c['failed_runs']}"
        assert c["failed_runs"] == []


def test_system_scan_v1_failures_are_only_vacuous(system_scan):
    """V1 若失败必须是真失败；空真的 run 必须被显式列出。"""
    c = system_scan["checks"]["V1"]
    assert c["failed_runs"] == []
    for rid in system_scan["v1_vacuous"]:
        assert rid in {r["run_id"] for r in system_scan["non_success_runs"]}, \
            f"{rid} 是空真，必须同时是非 success run"


def test_report_contains_the_system_scan_section():
    if not REPORT_PATH.exists():
        pytest.skip("尚未生成 innovation2_report.md")
    md = REPORT_PATH.read_text(encoding="utf-8")
    assert "系统级扫描" in md
    for key in ("V1", "V2", "V3", "V4", "V5", "V6"):
        assert key in md


# --------------------------------------------------------------------------
# 10. 终端走查层（"不能只是一堆 PASS"）
# --------------------------------------------------------------------------

@pytest.fixture(scope="module")
def terminal_text():
    try:
        t = trace_run(DEFAULT_RUN)
    except Exception:            # pragma: no cover
        pytest.skip(f"{DEFAULT_RUN} 存档不可用")
    secs = build_terminal_sections({DEFAULT_RUN: t}, scan_all_runs())
    return render_terminal(secs), secs


def test_terminal_output_has_real_content_not_just_pass(terminal_text):
    """终端输出必须带真实句子 / 字段值 / 工具调用，不能只有 PASS。"""
    text, _ = terminal_text
    # 真实结论句原文
    assert "结论句[" in text
    # 真实台账路径与字段路径
    assert "evidence_ledger.entries[0]" in text
    assert "structured_judgement." in text
    # 真实工具调用名与参数
    assert "B02.get_environment_history" in text
    assert "arguments=" in text
    # 真实状态与时间
    assert "status=ok" in text
    assert "2026-10-21T09:30:00" in text
    # 不许退化成一行行 PASS
    assert "V1: PASS" not in text


def test_terminal_sections_are_twelve_and_named(terminal_text):
    _, secs = terminal_text
    assert len(secs) == 12
    titles = " ".join(s["title"] for s in secs)
    for needed in ("审计对象", "溯源链实录", "原始回答", "证据身份",
                   "工具调用总账", "V1–V6", "强弱分档", "降级证据",
                   "限制陈述", "全库系统扫描", "证明了什么", "复现命令"):
        assert needed in titles, f"终端走查缺少章节：{needed}"


def test_terminal_check_lines_carry_evidence(terminal_text):
    """V1–V6 那一节必须逐项附真实依据（不是光 PASS/FAIL）。"""
    text, _ = terminal_text
    assert "V1 溯源链闭合 —— 挑出结论句" in text
    assert "V2 真调用" in text
    assert "V3 红线（谈天气必须真调天气工具）" in text
    assert "V4 红线（谈知识必须真检索）" in text
    assert "V5 四类证据角色可区分" in text
    assert "V6 台账自洽 —— total_calls=" in text
    assert "认领依据" in text


def test_terminal_rendering_is_deterministic():
    try:
        t = trace_run(DEFAULT_RUN)
    except Exception:            # pragma: no cover
        pytest.skip(f"{DEFAULT_RUN} 存档不可用")
    secs = build_terminal_sections({DEFAULT_RUN: t}, scan_all_runs())
    assert render_terminal(secs) == render_terminal(secs)


def test_terminal_does_not_change_spec_artifacts():
    """终端层只负责呈现 —— 规格固定格式不能被它改坏。"""
    try:
        t = trace_run(DEFAULT_RUN)
    except Exception:            # pragma: no cover
        pytest.skip(f"{DEFAULT_RUN} 存档不可用")
    before = to_spec_format(t)
    build_terminal_sections({DEFAULT_RUN: t}, scan_all_runs())
    assert to_spec_format(t) == before
