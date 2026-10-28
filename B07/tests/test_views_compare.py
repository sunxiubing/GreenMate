"""B07 第三遍 · 视图四 | 场景回放与对比 的不变量测试（离线，不联网、不调 LLM）

覆盖的东西：
  t1 **三层归因可复算**：同一对 run 连算两次，三层差异与归因**完全相同**
  t2 **★ R4 必被触发**：构造「同输入不同输出」的两个 run，
     归因必须明确标为「同一条件下重跑结果不一致（不稳定性）」，
     **不许**被归成"因为环境不同"
  t3 **组 1（S1↔S2）**：归因命中 **R2**，且归因文本包含 "NORMAL"
  t4 **组 2（S1↔S3 干净对照）**：inputs 只在 external_conditions 存在性上不同，
     稳定判定与 predicted_attention 未变，combined_attention 变了
  t5 **组 3（S4↔S5）**：归因命中 **R1**，且归因文本同时包含 "ok" 与 "unavailable"
  t6 **四视图统一检查**：check_same_analysis("run_082") 通过
     （U1/U2/U3 全 True；U3 **必须包含打桩**）

跑法：`python -m pytest B07/tests -q`
"""

from __future__ import annotations

import json
import shutil

import pytest

from B07.views import view_compare_data
from B07.views_diff import diff_runs

# 组 1：S1↔S2（末点温度/湿度相同，光照与稳定判定不同）
RUN_S1 = "run_012"
RUN_S2 = "run_015"
# 组 2：S1↔S3（只改"是否接入外部天气"这一项的干净对照）
RUN_S3 = "run_023"
# 组 3：S4↔S5（同一份外部证据从「可用且反向」变成「不可用」）
RUN_S4 = "run_057"
RUN_S5 = "run_084"
# 统一检查靶子
RUN_CHECK = "run_082"


# --------------------------------------------------------------------------
# t1 三层归因可复算
# --------------------------------------------------------------------------

def test_attribution_is_reproducible():
    """同一对 run 连算两次，三层差异与归因必须完全相同。"""
    d1 = diff_runs(RUN_S1, RUN_S2)
    d2 = diff_runs(RUN_S1, RUN_S2)

    assert d1["inputs"] == d2["inputs"]
    assert d1["evidence"] == d2["evidence"]
    assert d1["state"] == d2["state"]
    assert d1["attribution"] == d2["attribution"]
    assert d1["data_sha256"] == d2["data_sha256"]
    assert d1["attribution_complete"] is True


def test_compare_data_matches_diff_runs():
    """视图四的数据层与归因器必须给出一致结果（同一个 load_run）。"""
    a = view_compare_data(RUN_S1, RUN_S2)
    b = diff_runs(RUN_S1, RUN_S2)
    assert a["attribution"]["rule"] == b["attribution"]["rule"]
    assert a["data_sha256"] == b["data_sha256"]
    assert a["view"] == "compare"


# --------------------------------------------------------------------------
# t2 ★ R4：条件没变，结果却变了
# --------------------------------------------------------------------------

def _clone_archive(src_run: str, dst_dir, dst_name: str, mutate) -> str:
    """把一个存档拷成新 run，改几处字段后写盘（只写临时目录）。"""
    import B07.views as views

    src = views.RUNS_DIR / src_run / "run.json"
    data = json.loads(src.read_text(encoding="utf-8"))
    data["run_id"] = dst_name
    mutate(data)
    d = dst_dir / dst_name
    d.mkdir(parents=True, exist_ok=True)
    (d / "run.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return dst_name


def test_r4_instability_is_detected(monkeypatch, tmp_path):
    """★ 同输入不同输出 → 必须归因为「不稳定性」，不许说"因为环境不同"。"""
    import B07.views as views

    def _mutate(data: dict) -> None:
        # 只动**状态层**：稳定判定翻转 + 关注等级变化。
        # 输入层（analysis_time / current_state / history / external_conditions）
        # 一个字都不动 —— 这正是"条件没变结果却变了"。
        sj = data.setdefault("structured_judgement", {})
        rt = sj.setdefault("recent_trend", {})
        rt["stability_verdict"] = "不稳定"
        rt["conclusion"] = "人为构造：同样输入下判成了不稳定"
        ca = data.setdefault("combined_attention", {})
        ca["value"] = "watch_internal"
        ca["reason"] = "人为构造：同样输入下的关注等级也变了"

    a_name = _clone_archive(RUN_S1, tmp_path, "run_r4_a", lambda d: None)
    b_name = _clone_archive(RUN_S1, tmp_path, "run_r4_b", _mutate)

    monkeypatch.setattr(views, "RUNS_DIR", tmp_path)
    d = diff_runs(a_name, b_name)

    # ---- 前置核对：输入层与证据层都没变，状态层变了 ----
    assert d["inputs"]["changed"] is False
    assert d["evidence"]["changed"] is False
    assert d["state"]["changed"] is True

    # ---- 归因必须是 R4（不稳定性）----
    assert d["attribution"]["rule"] == "R4"
    assert d["instability_detected"] is True
    assert "同一条件下重跑结果不一致（不稳定性）" in d["attribution"]["text"]
    # ★ 不许被归成"因为环境不同"
    assert "因为环境不同" not in d["attribution"]["text"]
    # 必须列出具体不一致的字段
    assert "recent_trend.stability_verdict" in d["attribution"]["text"]
    assert "combined_attention.value" in d["attribution"]["text"]


def test_r4_check_detects_same_input_different_output(monkeypatch, tmp_path):
    """回放自检：同一场景、同一份输入，state 却变了 → 显式报不稳定。"""
    import B07.views as views

    def _mutate(data: dict) -> None:
        sj = data.setdefault("structured_judgement", {})
        rt = sj.setdefault("recent_trend", {})
        rt["stability_verdict"] = "不稳定"
        ca = data.setdefault("combined_attention", {})
        ca["value"] = "watch_internal"

    a_name = _clone_archive(RUN_S1, tmp_path, "run_r4_a2", lambda d: None)
    b_name = _clone_archive(RUN_S1, tmp_path, "run_r4_b2", _mutate)
    monkeypatch.setattr(views, "RUNS_DIR", tmp_path)

    d = diff_runs(a_name, b_name)
    assert d["inputs"]["changed"] is False
    assert d["state"]["changed"] is True
    assert d["instability_detected"] is True
    assert d["attribution"]["rule"] == "R4"


# --------------------------------------------------------------------------
# t3 组 1：S1↔S2 → R2，归因文本包含 NORMAL
# --------------------------------------------------------------------------

def test_group1_s1_vs_s2_hits_r2_and_mentions_normal():
    d = diff_runs(RUN_S1, RUN_S2)
    attr = d["attribution"]

    assert attr["rule"] == "R2"
    assert "NORMAL" in attr["text"]

    # 归因文本必须引用具体字段值，不许写"因为环境不同"
    assert "因为环境不同" not in attr["text"]
    assert "recent_trend.stability_verdict" in attr["text"]
    assert "predicted_attention" in attr["text"]

    # 关键结论：B06 预测两侧都是 NORMAL，差别出在判据层
    assert any("B06 预测两侧都是 NORMAL" in n for n in attr["notes"])

    # 证据层：两侧都没有台账 → **无法比较**，不是"证据相同"
    assert d["evidence"]["comparable"] is False
    assert any("无法比较" in n for n in d["evidence"]["notes"])


# --------------------------------------------------------------------------
# t4 组 2：S1↔S3 干净对照
# --------------------------------------------------------------------------

def test_group2_s1_vs_s3_is_a_clean_control():
    """S1↔S3：只改"是否接入外部天气"这一项，其余输入逐字节相同。"""
    d = diff_runs(RUN_S1, RUN_S3)

    # ---- 输入层：只有 external_conditions 这一项变了 ----
    # （"是否存在" + "signal" 两条都属于同一项：是否接入外部天气）
    changed_inputs = set(d["inputs"]["changed_keys"])
    assert changed_inputs <= {"external_conditions（是否存在）",
                              "external_conditions.signal"}, changed_inputs
    assert "external_conditions（是否存在）" in changed_inputs
    for it in d["inputs"]["items"]:
        if not it["key"].startswith("external_conditions"):
            assert it["changed"] is False, (it["key"], it["a"], it["b"])

    # history 逐点相同
    hist = next(it for it in d["inputs"]["items"]
                if it["key"] == "history（序列逐点）")
    assert hist["changed"] is False
    assert hist["diff_points"] == 0

    # ---- 状态层：稳定判定与 predicted_attention 未变，combined_attention 变了 ----
    st = {it["key"]: it for it in d["state"]["items"]}
    assert st["recent_trend.stability_verdict"]["changed"] is False
    assert st["predicted_attention"]["changed"] is False
    assert st["combined_attention.value"]["changed"] is True

    # ---- 归因文本要点出"这是外部条件接入带来的唯一效果" ----
    attr = d["attribution"]
    assert attr["rule"] in ("R1", "R2", "R3")
    assert any("唯一效果" in n for n in attr["notes"]), attr["notes"]
    assert "因为环境不同" not in attr["text"]


# --------------------------------------------------------------------------
# t5 组 3：S4↔S5 → R1，文本含 ok 与 unavailable
# --------------------------------------------------------------------------

def test_group3_s4_vs_s5_hits_r1_and_mentions_ok_and_unavailable():
    d = diff_runs(RUN_S4, RUN_S5)
    attr = d["attribution"]

    assert attr["rule"] == "R1"
    text = attr["text"]
    assert "ok" in text
    assert "unavailable" in text

    # 归因链：外部证据失效 → 分歧退化为缺失 → 退让到只看内部证据
    assert any("同一份外部证据从「ok」变成「unavailable」" in n
               for n in attr["notes"])
    assert any("分歧退化为缺失" in n for n in attr["notes"])
    assert any("退让到只看内部证据" in n for n in attr["notes"])

    # 证据层必须可比（两侧都有台账）
    assert d["evidence"]["comparable"] is True


def test_evidence_usable_count_uses_status_ok_not_adopted_entries():
    """★ 证据层「可用条数」必须按 status==ok 统计，不能用 adopted_entries。"""
    d = diff_runs(RUN_S4, RUN_S5)
    item = next(it for it in d["evidence"]["items"]
                if it["key"] == "可用条数（status==ok）")
    # S4 四条全 ok；S5 的 B03 是 unavailable → 真正有效的只有 3 条
    assert item["a"] == 4 and item["b"] == 3
    adopted = next(it for it in d["evidence"]["items"]
                   if it["key"].startswith("台账 counts.adopted_entries"))
    assert adopted["a"] == 4 and adopted["b"] == 4  # 采用数仍是 4


# --------------------------------------------------------------------------
# t6 四视图统一检查
# --------------------------------------------------------------------------

def test_check_same_analysis_passes_for_run_082():
    from B07.views_check import check_same_analysis

    res = check_same_analysis(RUN_CHECK)

    assert res["identity_consistent"] is True
    assert res["zero_recompute"] is True
    assert res["source_sha256"]
    assert len(res["views_checked"]) == 4

    ids = [d["id"] for d in res["detail"]]
    assert ids == ["U1", "U2", "U3", "U4"]
    for d in res["detail"]:
        assert d["passed"] is True, (d["id"], d["actual"])
    assert res["passed"] is True


def test_zero_recompute_really_stubs_everything():
    """★ U3 必须真的打桩（openai / httpx / requests / B03 / B06 / B04）。"""
    from B07.views_check import check_zero_recompute

    u3 = check_zero_recompute(RUN_CHECK)
    assert u3["passed"] is True, u3["failed"]

    stubbed = " ".join(u3["stubbed"])
    for must in ("openai", "httpx", "requests", "B03.weather",
                 "B06.inference", "B04.retrieve"):
        assert must in stubbed, stubbed

    # 八个步骤（四个视图的装配 + 渲染）全部成功
    assert len(u3["steps"]) == 8
    assert all(s["ok"] for s in u3["steps"])


def test_check_reports_fail_explicitly(monkeypatch, tmp_path):
    """不存在的 run 必须显式失败，不许静默通过。"""
    import B07.views as views

    monkeypatch.setattr(views, "RUNS_DIR", tmp_path)
    with pytest.raises(FileNotFoundError):
        diff_runs("run_999", "run_999")
