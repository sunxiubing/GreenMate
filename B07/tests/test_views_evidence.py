"""B07 第三遍 · 视图三 | 证据空间 的不变量测试（离线，不联网、不调 LLM）

覆盖的东西：
  t1 **只读存档**：把 openai / httpx / requests / B03 天气 / B06 推理 /
     B04 知识检索全部打桩成"一调用就抛"，视图三照样渲染成功
  t2 **★ 可用条数按 status==ok 统计**：run_084 的可用条数必须是 **3**，
     绝不能是 counts.adopted_entries（=4）—— 用 adopted_entries 会把
     降级场景里最该暴露的缺失抹平
  t3 **缺失必须看得见**：run_084 的渲染里必须出现 B03、unavailable、
     "weather provider unavailable" 原文、以及「它影响了什么判断」
  t4 **★ 反抹平**：run_082 的渲染里必须**同时**出现「支持」与「反向」，
     且不得出现"都支持 WATCH""全部支持""一致支持"这类把分歧抹平的表述
  t5 **对照组不出现反向徽章**：run_106 四份证据都是支持方向
  t6 **没有台账的 run 不报错**：run_012 早于 S4 的证据层，必须显式标注
     "本次运行没有证据台账"，而不是假装有证据

跑法：`python -m pytest B07/tests -q`
"""

from __future__ import annotations

import pytest

from B07.views import (
    load_run,
    render_evidence,
    view_evidence_data,
)

#: 分歧齐全：历史+模型支持提高关注，天气提示未来可能缓解，知识只支持部分
RUN_CONFLICT = "run_082"
#: 天气缺失：external_unavailable + B03 status=unavailable
RUN_MISSING = "run_084"
#: 对照组：四份证据全是 supporting，has_conflict=False
RUN_CONTROL = "run_106"
#: S1 稳态 run，早于 S4 的证据层 —— 没有台账
RUN_NO_LEDGER = "run_012"


def _boom(*a, **kw):
    raise RuntimeError("不该被调用：视图三不允许重新取数")


# --------------------------------------------------------------------------
# t1 只读存档
# --------------------------------------------------------------------------

def test_render_only_reads_the_archive(monkeypatch):
    """把所有能取数 / 跑模型 / 检索知识的东西打成"一调用就抛"，照样渲染成功。"""
    import socket

    import B01.llm as b01_llm
    import B03.weather as b03_weather
    import B04.retrieve as b04_retrieve
    import B06.inference.inference as b06_inference
    import httpx
    import openai
    import requests

    import B02.database.store as b02_store

    monkeypatch.setattr(b01_llm, "get_client", _boom)
    monkeypatch.setattr(b01_llm, "ask", _boom)
    monkeypatch.setattr(openai, "OpenAI", _boom)
    monkeypatch.setattr(openai, "AzureOpenAI", _boom, raising=False)
    monkeypatch.setattr(httpx, "Client", _boom)
    monkeypatch.setattr(httpx, "AsyncClient", _boom)
    monkeypatch.setattr(httpx, "get", _boom)
    monkeypatch.setattr(httpx, "post", _boom)
    monkeypatch.setattr(requests, "get", _boom)
    monkeypatch.setattr(requests, "post", _boom)
    monkeypatch.setattr(b03_weather, "get_weather", _boom)
    monkeypatch.setattr(b03_weather, "_api_request", _boom)
    monkeypatch.setattr(b06_inference, "predict_window", _boom)
    monkeypatch.setattr(b06_inference, "load_for_inference", _boom)
    monkeypatch.setattr(b04_retrieve, "retrieve", _boom)
    monkeypatch.setattr(b02_store, "query_samples", _boom, raising=False)
    monkeypatch.setattr(b02_store, "get_samples", _boom, raising=False)

    real_socket = socket.socket

    class NoNet(real_socket):        # type: ignore[misc]
        def connect(self, *a, **kw):
            raise RuntimeError("不该被调用：视图三不允许联网")

        def connect_ex(self, *a, **kw):
            raise RuntimeError("不该被调用：视图三不允许联网")

    monkeypatch.setattr(socket, "socket", NoNet)

    text = render_evidence(RUN_CONFLICT)
    assert "视图三 | 证据空间" in text
    assert "历史证据 · B02" in text
    assert "工具 / 运行状态" in text


def test_evidence_data_is_deterministic():
    a = view_evidence_data(RUN_CONFLICT)
    b = view_evidence_data(RUN_CONFLICT)
    assert a["data_sha256"] == b["data_sha256"]
    assert render_evidence(RUN_CONFLICT) == render_evidence(RUN_CONFLICT)


# --------------------------------------------------------------------------
# t2 ★ 可用条数必须按 status==ok 统计
# --------------------------------------------------------------------------

def test_usable_count_uses_status_ok_not_adopted_entries():
    """★ run_084：可用条数必须是 3，不是 counts.adopted_entries(=4)。"""
    bundle = load_run(RUN_MISSING)
    ledger = bundle["evidence_ledger"]
    entries = ledger["entries"]

    # 前提核对：台账自己说 adopted_entries=4，但其中一条 status=unavailable
    assert ledger["counts"]["adopted_entries"] == 4
    assert sum(1 for e in entries if e["status"] == "ok") == 3

    d = view_evidence_data(RUN_MISSING)
    assert d["counts"]["usable"] == 3
    assert d["counts"]["unusable"] == 1
    assert d["counts"]["entries"] == 4
    # ★ 绝不能等于 adopted_entries
    assert d["counts"]["usable"] != d["counts"]["ledger_adopted_entries"]
    assert d["counts"]["usable"] != ledger["counts"]["adopted_entries"]
    # 缺失清单必须列出来
    assert len(d["not_ok_entries"]) == 1
    assert d["not_ok_entries"][0]["source_type"] == "B03"
    assert d["not_ok_entries"][0]["status"] == "unavailable"


def test_usable_count_matches_entries_scanned_by_hand():
    """可用条数 = 逐条扫 status==ok，与手数一致。"""
    for rid in (RUN_CONFLICT, RUN_MISSING, RUN_CONTROL):
        entries = load_run(rid)["evidence_ledger"]["entries"]
        by_hand = [e["evidence_id"] for e in entries if e["status"] == "ok"]
        d = view_evidence_data(rid)
        assert d["counts"]["usable"] == len(by_hand)


# --------------------------------------------------------------------------
# t3 缺失必须看得见
# --------------------------------------------------------------------------

def test_missing_weather_is_still_rendered():
    """★ 天气不可用 ≠ 不显示：B03 这一块必须照常出现。"""
    text = render_evidence(RUN_MISSING)
    assert "B03" in text
    assert "unavailable" in text
    assert "weather provider unavailable" in text
    assert "它影响了什么判断" in text
    # 不许把"没有内容"当成"没有这条证据"
    assert "（没有内容 —— 这次取数失败了）" in text


def test_missing_impact_comes_from_the_archive_not_a_template():
    """「它影响了什么判断」必须来自 unavailable_context + combined_attention.reason。"""
    bundle = load_run(RUN_MISSING)
    ca = bundle["combined_attention"]
    uc = (bundle["evidence_ledger"]["entries"][2]["ext"] or {}).get(
        "unavailable_context") or {}

    d = view_evidence_data(RUN_MISSING)
    weather = [b for b in d["blocks"] if b["source_type"] == "B03"][0]
    impact = " ".join(weather["impact"])

    assert ca["reason"] in impact                 # 判定理由原文
    assert str(ca["value"]) in impact             # watch_internal
    assert str(uc.get("signal")) in impact       # external_unavailable
    assert str(uc.get("basis")) in impact        # 未做任何判定

    text = render_evidence(RUN_MISSING)
    assert ca["reason"] in text


def test_unavailable_error_is_verbatim():
    """失败原因必须是存档里的 error 原文，不许改写。"""
    bundle = load_run(RUN_MISSING)
    entry = [e for e in bundle["evidence_ledger"]["entries"]
             if e["source_type"] == "B03"][0]
    text = render_evidence(RUN_MISSING)
    assert entry["error"] in text


# --------------------------------------------------------------------------
# t4 ★ 反抹平
# --------------------------------------------------------------------------

def test_divergence_is_visible_in_the_render():
    """★ run_082 必须同时出现「支持」与「反向」，不得被抹平成一致。"""
    text = render_evidence(RUN_CONFLICT)
    assert "支持" in text
    assert "反向" in text

    for banned in ("都支持 WATCH", "全部支持", "一致支持",
                   "都支持", "全部一致支持"):
        assert banned not in text, f"渲染结果里出现了抹平表述：{banned}"


def test_direction_badges_come_from_the_archive():
    """方向徽章必须取存档里已判定的 direction。"""
    d = view_evidence_data(RUN_CONFLICT)
    by_type = {b["source_type"]: b for b in d["blocks"]}
    assert by_type["B02"]["direction"] == "supporting"
    assert by_type["B06"]["direction"] == "supporting"
    assert by_type["B03"]["direction"] == "opposing"
    assert by_type["B04"]["direction"] == "supporting"
    assert d["divergence"]["has_conflict"] is True
    assert d["combined_attention"]["value"] == "conflict_unresolved"
    assert d["combined_attention"]["external_signal"] == "external_improving"


def test_all_four_blocks_always_present():
    """四类证据必须**永远**显示成四块，缺一块也要显示成缺。"""
    d = view_evidence_data(RUN_CONFLICT)
    assert [b["source_type"] for b in d["blocks"]] == ["B02", "B06", "B03", "B04"]


# --------------------------------------------------------------------------
# t5 对照组不出现反向徽章
# --------------------------------------------------------------------------

def test_control_run_has_no_opposing_badge():
    """run_106 四份证据全是 supporting —— 渲染里不许出现「反向」徽章。"""
    d = view_evidence_data(RUN_CONTROL)
    for b in d["blocks"]:
        assert b["direction"] == "supporting"
    assert d["divergence"]["has_conflict"] is False

    text = render_evidence(RUN_CONTROL)
    assert "反向" not in text
    assert "支持" in text


# --------------------------------------------------------------------------
# t6 没有台账的 run
# --------------------------------------------------------------------------

def test_run_without_ledger_renders_explicitly():
    """run_012 早于证据层：不报错，且必须显式说"没有证据台账"。"""
    assert "evidence_ledger" not in load_run(RUN_NO_LEDGER)

    d = view_evidence_data(RUN_NO_LEDGER)
    assert d["has_ledger"] is False

    text = render_evidence(RUN_NO_LEDGER)
    assert "本次运行没有证据台账（该 run 早于 S4 的证据层）" in text
    # 四块仍然显示为「不存在」——"不存在"和"没显示"是两回事
    assert "不存在" in text
    for b in d["blocks"]:
        assert b["absent"] is True
        assert b["status"] == "absent"


# --------------------------------------------------------------------------
# Web 版 —— 必须复用同一个数据层
# --------------------------------------------------------------------------

def test_web_evidence_api_reuses_the_same_data_layer():
    from fastapi.testclient import TestClient

    from B07.views_web import app

    client = TestClient(app)
    r = client.get("/api/evidence?run_id=" + RUN_MISSING)
    assert r.status_code == 200
    assert r.json()["data_sha256"] == view_evidence_data(RUN_MISSING)["data_sha256"]


def test_web_unknown_run_is_explicit_404():
    from fastapi.testclient import TestClient

    from B07.views_web import app

    client = TestClient(app)
    r = client.get("/api/evidence?run_id=run_999")
    assert r.status_code == 404
    assert "找不到 run 存档" in r.json()["detail"]


def test_web_page_is_offline():
    from fastapi.testclient import TestClient

    from B07.views_web import app

    client = TestClient(app)
    page = client.get("/")
    assert page.status_code == 200
    assert "https://" not in page.text
    assert "视图三" in page.text
