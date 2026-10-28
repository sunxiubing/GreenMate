"""B07 第三遍 · 视图二 | 状态演化 的不变量测试（离线，不联网、不调 LLM）

覆盖的东西：
  t1 **曲线数据只来自 history**：把 B02 取数 / B03 天气 / B06 推理 / openai /
     httpx / requests 全部打桩成"一调用就抛"，视图二照样渲染成功
  t2 **过程量不依赖 structured_judgement**：把 structured_judgement 换成空 dict 的
     run 副本，过程量结果必须与原来**完全相同**
  t3 **C1~C5 五条机器判定全部成立**（run_012 / run_015）
  t4 **对照检查**：run_023（与 run_012 同一段窗口）的过程量 == run_012 的过程量
  t5 **不许编造时点标记**：模型状态/关注等级的演化在单 run 内做不出来，必须
     显式说明；渲染结果里不许出现没有档案支撑的时点标记

跑法：`python -m pytest B07/tests -q`
"""

from __future__ import annotations

import copy

import pytest

from B07.views import (
    compare_data,
    compute_process_metrics,
    control_check,
    evolution_data_from_bundle,
    find_same_window_runs,
    load_run,
    render_evolution,
    render_evolution_compare,
    view_evolution_data,
)

#: S1 稳态 run（末点 32.8℃，窗口 2026-10-21 13:30→16:00）
RUN_S1 = "run_012"
#: S2 渐进漂移 run（末点 32.8℃，窗口 2026-10-20 09:30→12:00）
RUN_S2 = "run_015"
#: 与 run_012 用的是同一段窗口的 run —— 对照检查用
RUN_CONTROL = "run_023"


def _boom(*a, **kw):
    raise RuntimeError("不该被调用：视图二不允许重新计算")


# --------------------------------------------------------------------------
# t1 曲线数据只来自 history
# --------------------------------------------------------------------------

def test_render_only_uses_history(monkeypatch):
    """把所有能取数 / 跑模型的东西打成"一调用就抛"，视图二照样渲染成功。"""
    import socket
    import sys

    import B01.llm as b01_llm
    import B03.weather as b03_weather
    import B06.inference.inference as b06_inference
    import httpx
    import requests

    # B02 取数（视图二绝对不许重新查 B02）
    import B02.database.store as b02_store

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
    monkeypatch.setattr(b02_store, "query_samples", _boom, raising=False)
    monkeypatch.setattr(b02_store, "get_samples", _boom, raising=False)

    real_socket = socket.socket

    class NoNet(real_socket):        # type: ignore[misc]
        def connect(self, *a, **kw):
            raise RuntimeError("不该被调用：视图二不允许联网")

        def connect_ex(self, *a, **kw):
            raise RuntimeError("不该被调用：视图二不允许联网")

    monkeypatch.setattr(socket, "socket", NoNet)

    text = render_evolution(RUN_S2)
    assert "视图二 | 状态演化" in text
    assert "history" in text
    assert sys.modules is not None


def test_curve_points_come_from_history():
    """曲线上的每个点都必须能在存档的 history 里找到。"""
    bundle = load_run(RUN_S2)
    d = view_evolution_data(RUN_S2)
    hist = bundle["history"]
    assert len(d["points"]) == len(hist)
    for p, h in zip(d["points"], hist):
        assert p["timestamp"] == h["timestamp"]
        assert p["temperature"] == h["temperature"]
        assert p["humidity"] == h["humidity"]
        assert p["lux"] == h["lux"]
    # 窗口核对必须一致
    assert d["alignment"]["ok"] is True


def test_evolution_data_is_deterministic():
    a = view_evolution_data(RUN_S2)
    b = view_evolution_data(RUN_S2)
    assert a["data_sha256"] == b["data_sha256"]
    assert render_evolution(RUN_S2) == render_evolution(RUN_S2)


# --------------------------------------------------------------------------
# t2 过程量不依赖 structured_judgement
# --------------------------------------------------------------------------

def test_process_metrics_do_not_depend_on_structured_judgement():
    """把 structured_judgement 换成空 dict，过程量必须**完全相同**。"""
    bundle = load_run(RUN_S2)
    original = evolution_data_from_bundle(bundle)

    stripped = copy.deepcopy(bundle)
    stripped["structured_judgement"] = {}
    stripped_bundle = evolution_data_from_bundle(stripped)

    assert original["process"] == stripped_bundle["process"]
    assert compute_process_metrics(bundle["history"]) == \
        compute_process_metrics(stripped["history"])


def test_process_metrics_only_read_history():
    """过程量函数只吃 history 列表 —— 给它别的字段它也用不上。"""
    metrics = compute_process_metrics(load_run(RUN_S1)["history"])
    assert metrics["point_count"] == 6
    assert set(metrics["metrics"]) == {"temperature", "humidity", "lux"}
    temp = metrics["metrics"]["temperature"]
    for key in ("start_value", "end_value", "delta", "range_width", "max_step",
                "monotone_up", "monotone_down", "trend_shape", "sparkline"):
        assert key in temp


def test_trend_shape_is_a_rule_based_classification():
    """趋势形状必须落在规定的取值集合里，且依据写在数据里。"""
    d = view_evolution_data(RUN_S2)
    shapes = d["trend_shapes"]
    for m in d["process"]["metrics"].values():
        assert m["trend_shape"] in shapes
        assert m["shape_basis"]


# --------------------------------------------------------------------------
# t3 C1~C5
# --------------------------------------------------------------------------

def test_c1_to_c5_all_pass():
    """C1~C5 五条相对比较的机器判定必须全部成立。"""
    d = compare_data(RUN_S1, RUN_S2)
    got = {c["id"]: c for c in d["assertions"]}
    assert set(got) == {"C1", "C2", "C3", "C4", "C5"}
    for cid in ("C1", "C2", "C3", "C4", "C5"):
        assert got[cid]["passed"] is True, f"{cid} 未通过：{got[cid]}"
    # C1：末点温度几乎相同 —— 只看当前值分不出 S1/S2
    assert abs(got["C1"]["delta"]) <= 0.5
    assert "不能" in d["reverse_check"]


def test_compare_render_shows_relative_axis_and_day_warning():
    text = render_evolution_compare(RUN_S1, RUN_S2)
    assert "视图二 | 状态演化" in text
    assert "不在同一天" in text          # 绝对时间不可对齐，必须显式提示
    assert "相对时间" in text            # X 轴用窗口内相对位置
    assert "C1" in text and "C5" in text
    assert "process" not in text        # 顺手确认没有把原始字段名直接甩出来


def test_compare_uses_relative_axis_only():
    d = compare_data(RUN_S1, RUN_S2)
    assert len(d["axis"]) == 6
    rel_a = [p["relative_minute"] for p in d["a"]["points"]]
    rel_b = [p["relative_minute"] for p in d["b"]["points"]]
    assert rel_a == rel_b == [0, 30, 60, 90, 120, 150]
    # 两个 run 的绝对时间确实不在同一天
    assert d["same_calendar_day"] is False
    assert d["a"]["window"]["start"][:10] != d["b"]["window"]["start"][:10]


# --------------------------------------------------------------------------
# t4 对照检查
# --------------------------------------------------------------------------

def test_control_run_has_identical_process_metrics():
    """run_023 与 run_012 是同一段窗口 → 过程量必须完全相同。"""
    siblings = find_same_window_runs(RUN_S1)
    assert RUN_CONTROL in [s["run_id"] for s in siblings]

    ctrl = control_check(RUN_S1)
    item = [s for s in ctrl["siblings"] if s["run_id"] == RUN_CONTROL]
    assert item, "run_023 不在同段窗口的 run 列表里"
    assert item[0]["identical"] is True

    a = compute_process_metrics(load_run(RUN_S1)["history"])
    b = compute_process_metrics(load_run(RUN_CONTROL)["history"])
    assert a == b


def test_control_check_is_not_vacuous():
    """对照检查必须真的能发现不同 —— 换一个 run 就不该完全相同。"""
    a = compute_process_metrics(load_run(RUN_S1)["history"])
    b = compute_process_metrics(load_run(RUN_S2)["history"])
    assert a != b


# --------------------------------------------------------------------------
# t5 不许编造时点标记
# --------------------------------------------------------------------------

def test_no_fabricated_time_point_markers():
    """单 run 内做不出来的标记，不许以时点标记的形式出现。"""
    d = view_evolution_data(RUN_S2)
    markers = d["timeline"]["markers"]
    assert markers
    for m in markers:
        if m["scope"] == "point":
            # 时点标记的时刻必须来自 history 序列
            assert m["at"] in [p["timestamp"] for p in d["points"]]
            assert m["point_index"] is not None
        # 不许把"模型状态变化""关注等级提升"标成某个时刻
        assert "WATCH" not in m["kind"]
        assert "关注程度" not in m["kind"]
        assert "关注程度" not in m["text"]


def test_impossible_markers_are_explicitly_explained():
    """做不了的那两条必须显式写出来 —— 不许留空、不许用静态文案假装有。"""
    d = view_evolution_data(RUN_S2)
    na = d["timeline"]["not_available"]
    kinds = " ".join(x["kind"] for x in na)
    assert "模型由 NORMAL 变为 WATCH" in kinds
    assert "系统提高了关注程度" in kinds
    for x in na:
        assert x["reason"]

    text = render_evolution(RUN_S2)
    assert "无法标注" in text
    assert "做不了" in text
    assert "多个 run 之间对比" in text


def test_render_shows_the_process_not_just_the_conclusion():
    """必须能看到"过程"：逐点表 + sparkline + 过程量。"""
    text = render_evolution(RUN_S2)
    assert "逐点读数" in text
    assert "▁" in text and "█" in text          # sparkline 方块字符
    assert "持续上升" in text                    # run_015 的温度趋势形状
    assert "只从 history 序列算" in text


# --------------------------------------------------------------------------
# Web 版（可选交付物）—— 必须复用同一个数据层
# --------------------------------------------------------------------------

def test_web_evolution_api_reuses_the_same_data_layer():
    from fastapi.testclient import TestClient

    from B07.views_web import app

    client = TestClient(app)
    r = client.get("/api/evolution?run_id=" + RUN_S2)
    assert r.status_code == 200
    assert r.json()["data_sha256"] == view_evolution_data(RUN_S2)["data_sha256"]

    rc = client.get(f"/api/evolution/compare?a={RUN_S1}&b={RUN_S2}")
    assert rc.status_code == 200
    assert rc.json()["data_sha256"] == compare_data(RUN_S1, RUN_S2)["data_sha256"]


def test_web_unknown_run_is_explicit_404():
    from fastapi.testclient import TestClient

    from B07.views_web import app

    client = TestClient(app)
    r = client.get("/api/evolution?run_id=run_999")
    assert r.status_code == 404
    assert "找不到 run 存档" in r.json()["detail"]


def test_web_page_is_offline():
    from fastapi.testclient import TestClient

    from B07.views_web import app

    client = TestClient(app)
    page = client.get("/")
    assert page.status_code == 200
    assert "https://" not in page.text
    assert "视图二" in page.text
