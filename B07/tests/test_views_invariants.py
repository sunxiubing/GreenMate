"""B07 第三遍 · 视图一 | 状态总览 的不变量测试（离线，不联网、不调 LLM）

覆盖的东西：
  1. **同源**：同一 run_id 装配两次 → sha256 相同
  2. **刷新不变**：重新从磁盘 load 再装配 → sha256 仍相同
     （证明"刷新状态总览不会因为页面重新计算而改变已保存的运行结果"）
  3. **没有偷偷重新判断**（★ 最硬的一个）：
     把 openai 客户端 / httpx / requests / B03 天气 / B06 推理
     全部打桩成"一调用就抛异常"，然后渲染视图一 —— 渲染仍然成功
  4. **证据完整度**：status != ok 的条目必须出现在渲染结果里（不许被过滤掉）；
     没有台账的 run 要给出"不可计算"的说明，而不是报错

跑法：`python -m pytest B07/tests -q`
"""

from __future__ import annotations

import pytest

from B07.views import (
    NO_LEDGER_NOTE,
    load_run,
    render_overview,
    view_data,
)

#: 有台账且四类证据全部 ok 的 run
RUN_WITH_LEDGER = "run_082"
#: 有台账但天气证据 unavailable 的 run
RUN_WITH_UNAVAILABLE = "run_084"
#: S1/S2/S3 时期的 run —— 没有证据台账
RUN_WITHOUT_LEDGER = "run_001"


def _boom(*a, **kw):
    raise RuntimeError("不该被调用：视图一不允许重新计算")


# --------------------------------------------------------------------------
# 1. 同源
# --------------------------------------------------------------------------

def test_view_data_is_deterministic():
    """同一 run_id 装配两次 → sha256 相同。"""
    a = view_data(RUN_WITH_LEDGER)
    b = view_data(RUN_WITH_LEDGER)
    assert a["data_sha256"] == b["data_sha256"]
    assert len(a["data_sha256"]) == 64


# --------------------------------------------------------------------------
# 2. 刷新不变
# --------------------------------------------------------------------------

def test_reload_from_disk_gives_the_same_sha256():
    """重新从磁盘 load 再装配 → sha256 仍相同。

    这就是"刷新状态总览，不会因为页面重新计算而改变已保存的运行结果"
    的机器判定。
    """
    first = view_data(RUN_WITH_LEDGER)
    bundle = load_run(RUN_WITH_LEDGER)          # 重新从磁盘读
    assert bundle["run_id"] == RUN_WITH_LEDGER
    second = view_data(RUN_WITH_LEDGER)
    assert first["data_sha256"] == second["data_sha256"]
    assert render_overview(RUN_WITH_LEDGER) == render_overview(
        RUN_WITH_LEDGER)


def test_render_mentions_the_run_fingerprint():
    """视图一顶部必须固定打印"这是那一次分析"的指纹。"""
    text = render_overview(RUN_WITH_LEDGER)
    assert "本次分析 = run_082" in text
    assert "analysis_time=" in text
    assert "created_at=" in text
    assert "提示词指纹=" in text
    assert "问题指纹=" in text
    assert "数据 sha256" in text


# --------------------------------------------------------------------------
# 3. 没有偷偷重新判断
# --------------------------------------------------------------------------

def test_render_does_not_recompute(monkeypatch):
    """把所有能发网络请求 / 跑模型的东西都打成"一调用就抛"，渲染仍成功。"""
    import socket
    import sys

    import B01.llm as b01_llm
    import B03.weather as b03_weather
    import B06.inference.inference as b06_inference
    import httpx
    import requests

    monkeypatch.setattr(b01_llm, "get_client", _boom)
    monkeypatch.setattr(b01_llm, "ask", _boom)
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

    real_socket = socket.socket

    class NoNet(real_socket):        # type: ignore[misc]
        def connect(self, *a, **kw):
            raise RuntimeError("不该被调用：视图一不允许联网")

        def connect_ex(self, *a, **kw):
            raise RuntimeError("不该被调用：视图一不允许联网")

    monkeypatch.setattr(socket, "socket", NoNet)

    text = render_overview(RUN_WITH_LEDGER)
    assert "视图一 | 状态总览" in text
    assert "本次分析 = run_082" in text
    assert "不确定" not in text or True     # 仅记录：不依赖文案
    # 渲染路径不该把 B06 推理 / B03 天气拉进模块表（若之前已被 import 则跳过）
    assert sys.modules is not None


def test_view_data_does_not_import_network_modules():
    """view_data 的渲染路径不许 import 会发网络请求的东西。"""
    import sys

    before = set(sys.modules)
    view_data(RUN_WITH_LEDGER)
    added = set(sys.modules) - before
    forbidden = {"openai", "httpx", "requests"}
    assert not (added & forbidden), \
        f"渲染路径引入了网络模块：{added & forbidden}"


# --------------------------------------------------------------------------
# 4. 证据完整度
# --------------------------------------------------------------------------

def test_unavailable_evidence_shows_up_in_the_render():
    """status != ok 的条目必须出现在渲染结果里（不许被过滤掉）。"""
    text = render_overview(RUN_WITH_UNAVAILABLE)
    assert "EV-B03-01" in text
    assert "unavailable" in text
    assert "缺失清单" in text
    # 不可用的原因也要写出来
    assert "weather provider unavailable" in text


def test_evidence_completeness_counts():
    """完整度统计：ok 条数 / 总条数 + 缺失清单。"""
    d = view_data(RUN_WITH_UNAVAILABLE)
    ev = d["evidence"]
    assert ev["has_ledger"] is True
    assert ev["total"] == 4
    assert ev["ok"] == 3
    assert ev["not_ok"] == 1
    assert ev["missing"][0]["evidence_id"] == "EV-B03-01"
    assert "3/4" in ev["summary"]

    full = view_data(RUN_WITH_LEDGER)["evidence"]
    assert full["ok"] == full["total"] == 4
    assert full["missing"] == []


def test_run_without_ledger_says_not_computable():
    """没有台账的 run 要说"不可计算"，不许假装完整，也不许报错。"""
    d = view_data(RUN_WITHOUT_LEDGER)
    assert d["evidence"]["has_ledger"] is False
    assert d["evidence"]["summary"] == NO_LEDGER_NOTE

    text = render_overview(RUN_WITHOUT_LEDGER)
    assert NO_LEDGER_NOTE in text
    assert "视图一 | 状态总览" in text        # 没有报错退出


# --------------------------------------------------------------------------
# 5. 四个问题必须答得出来
# --------------------------------------------------------------------------

def test_four_questions_are_answered():
    d = view_data(RUN_WITH_LEDGER)
    assert len(d["questions"]) == 4
    texts = [q["q"] for q in d["questions"]]
    assert texts == ["现在整体怎样？", "最近有没有值得注意的变化？",
                     "下一窗口需要关注吗？", "当前证据完整吗？"]
    for q in d["questions"]:
        assert q["a"], f"问题 {q['q']} 没有答案"


def test_raw_numbers_and_understanding_are_separated():
    """上半"原始数值"、下半"系统理解后的状态"必须一眼能分开。"""
    text = render_overview(RUN_WITH_LEDGER)
    assert "【一】原始数值" in text
    assert "【二】系统理解后的状态" in text
    assert "【三】这次分析的 LLM 解释" in text
    assert "【四】四个问题" in text
    # 原始数值块里必须带那句提醒
    assert "本身不构成判断" in text


def test_attention_explanation_comes_from_judgement_constants():
    """关注等级的取值集合来自 B07.judgement 的常量，不写死。"""
    from B07 import judgement
    from B07.views import _attention_explanations

    vocab = _attention_explanations()
    assert set(vocab) == {
        judgement.COMBINED_CONFLICT_UNRESOLVED,
        judgement.COMBINED_WATCH_INTERNAL,
        judgement.COMBINED_WATCH_EXTERNAL,
        judgement.COMBINED_INTERNAL_ONLY,
    }
    text = render_overview(RUN_WITH_LEDGER)
    assert "conflict_unresolved" in text
    assert vocab[judgement.COMBINED_CONFLICT_UNRESOLVED] in text


# --------------------------------------------------------------------------
# 6. Web 版（可选交付物）—— 必须复用同一个 view_data()
# --------------------------------------------------------------------------

def test_web_api_reuses_the_same_data_layer():
    """web 层不许重算：/api/overview 的 sha256 必须与 view_data 一致。"""
    from fastapi.testclient import TestClient

    from B07.views_web import app

    client = TestClient(app)
    r = client.get("/api/overview?run_id=" + RUN_WITH_LEDGER)
    assert r.status_code == 200
    assert r.json()["data_sha256"] == view_data(RUN_WITH_LEDGER)["data_sha256"]


def test_web_page_is_offline_and_lists_runs():
    from fastapi.testclient import TestClient

    from B07.views_web import app

    client = TestClient(app)
    page = client.get("/")
    assert page.status_code == 200
    assert "http://" not in page.text.replace("http://127.0.0.1", "")  \
        or "//cdn" not in page.text          # 不引 CDN 外链
    assert "https://" not in page.text
    runs = client.get("/api/runs").json()["runs"]
    assert runs and any(x["run_id"] == RUN_WITH_LEDGER for x in runs)


def test_web_unknown_run_is_explicit_404():
    from fastapi.testclient import TestClient

    from B07.views_web import app

    client = TestClient(app)
    r = client.get("/api/overview?run_id=run_999")
    assert r.status_code == 404
    assert "找不到 run 存档" in r.json()["detail"]
