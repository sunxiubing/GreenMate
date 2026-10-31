"""B08 第四遍 · 固定回归集的离线不变量测试（不调 LLM、不调天气 API）

★ 这一遍钉死的几件事：

  R1 **回归集够大、够分组**：13 个案例（文档要求 ≥10），每个场景至少 1 个案例，
     每个分组至少 2 个案例，负对照必须在集里。
  R2 **负对照必须被抓到**：在内存里改坏一份真实存档 → 必须报 FAIL。
     抓不到就说明整个回归集没有鉴别力（无效）。
  R3 **L1 复算层真的在复算**：--recompute 模式下每条 check 都带
     archived / recomputed / match / recomputable，**不是**拿存档值糊弄。
  R4 **期望值存在冻结件里**：冻结的期望清单覆盖全部案例，且与代码无关。
  R5 **跑两次能对比**：两次回归之间期望被改动 → diff 必须报出翻转。
  R6 **记录是追加的**：跑两次，记录行数翻倍（第二遍的 5 行不许被覆盖）。
  R7 **默认模式没变**：`verify`（不带 --recompute）仍然只读存档，
     记录里**没有** layers 字段 —— 第二遍的行为一字未改。

跑法：`python -m pytest B07/tests -q`
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from B07 import b08

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


# --------------------------------------------------------------------------
# R1 回归集的规模与分组
# --------------------------------------------------------------------------

def test_regression_set_has_at_least_ten_cases():
    cases = b08.REGRESSION_CASES
    assert len(cases) >= 10, f"文档要求 ≥10 个固定回归案例，现在只有 {len(cases)} 个"


def test_every_scenario_has_a_case():
    seen = {c["scenario_id"] for c in b08.REGRESSION_CASES}
    for sid in b08.SCENARIOS:
        assert sid in seen, f"场景 {sid} 没有任何回归案例"


def test_every_kind_has_at_least_two_cases():
    kinds: dict = {}
    for c in b08.REGRESSION_CASES:
        kinds.setdefault(c["kind"], []).append(c["case_id"])
    for kind, ids in kinds.items():
        assert len(ids) >= 2, f"分组 {kind} 只有 {len(ids)} 个案例（至少要 2 个）"


def test_negative_control_is_in_the_set():
    assert b08.NEGATIVE_CASE_ID == "NEG_must_fail"
    assert b08.NEGATIVE_CASE_ID not in {c["case_id"] for c in b08.REGRESSION_CASES}, \
        "负对照不许混进正常案例里"


def test_case_ids_are_unique():
    ids = [c["case_id"] for c in b08.REGRESSION_CASES]
    assert len(ids) == len(set(ids)), "案例 id 有重复"


# --------------------------------------------------------------------------
# R2 负对照必须被抓到
# --------------------------------------------------------------------------

def test_negative_control_is_caught():
    """在内存里改坏一帧温度 → L1 复算必然对不上 → 这个案例必须报 FAIL。"""
    neg = b08._run_negative_control(b08._regression_others())
    assert neg["verdict"] == "FAIL", "负对照没被抓到 —— 回归集没有鉴别力"
    l1 = neg["layers"]["L1_recompute"]
    assert l1["passed"] is False
    bad = [i for i in l1["items"] if i.get("match") is False]
    assert bad, "L1 复算层没有发现任何不一致 —— 复算层没在工作"
    assert neg["corruption"]["persisted"] is False, "负对照的坏数据不许落盘"


# --------------------------------------------------------------------------
# R3 L1 复算层真的在复算
# --------------------------------------------------------------------------

def test_recompute_mode_produces_l1_items():
    records, ok = b08.verify(["S1"], recompute=True)
    rec = records[0]
    layers = rec.get("layers") or {}
    assert "L1_recompute" in layers, "--recompute 模式没有挂 L1 复算层"
    items = layers["L1_recompute"]["items"]
    assert items, "L1 复算层没有产出任何复算项"
    for item in items:
        for key in ("check_id", "field", "archived", "recomputed", "match",
                    "recomputable"):
            assert key in item, f"L1 复算项缺字段 {key}"
    # 至少有真的重算出来的（不是全部标不可重算）
    assert any(i["recomputable"] and i["recomputed"] is not None
               for i in items), "L1 复算层没有任何一条真的重算过"
    # 存档值与重算值逐项一致（当前代码没坏）
    assert all(i["match"] is not False for i in items), \
        "L1 复算层发现存档值与重算值对不上"


def test_default_mode_is_unchanged():
    """第二遍的默认模式：只读存档，记录里没有 layers 字段。"""
    records, ok = b08.verify(["S1"])
    assert "layers" not in records[0], "默认模式不该挂复算层（第二遍行为不许变）"


def test_l1_uses_archive_raw_fields_only():
    """L1 的重算输入必须来自存档的原始字段。"""
    rec = next(r for r in [b08._run_case(c, b08._regression_others())
                            for c in b08.REGRESSION_CASES]
               if r["case_id"] == "S3_ext_deteriorating")
    items = {i["check_id"]: i for i in rec["layers"]["L1_recompute"]["items"]}
    sig = items["S3-2"]
    assert sig["recomputable"] is True
    assert sig["archived"] == "external_deteriorating"
    assert sig["recomputed"] == "external_deteriorating"
    assert "judge_external_points" in sig["note"]


# --------------------------------------------------------------------------
# R4 期望值存在冻结件里
# --------------------------------------------------------------------------

def test_frozen_expectations_cover_every_case():
    frozen = b08.load_expectations()
    if frozen is None:
        pytest.skip("还没有冻结的期望清单（先跑 python -m B07.b08 regression freeze）")
    cases = frozen.get("cases") or {}
    for c in b08.REGRESSION_CASES:
        assert c["case_id"] in cases, f"期望清单里没有 {c['case_id']}"
        assert cases[c["case_id"]]["checks"], \
            f"{c['case_id']} 的期望清单里没有逐条期望"
    assert frozen["negative_control"]["expected"] == "FAIL"


def test_second_pass_records_are_untouched():
    """第二遍的 5 行冻结记录不许被第四遍覆盖。"""
    recs = b08.read_records(b08.RECORDS_PATH)
    assert len(recs) == 5, f"第二遍的验证记录应该是 5 行，现在 {len(recs)} 行"
    assert all(r.get("verdict") == "PASS" for r in recs)


# --------------------------------------------------------------------------
# R5 / R6 跑两次：能对比、能追加
# --------------------------------------------------------------------------

def _run_into_tmp(tmp_path: Path, freeze: bool = False):
    exp = tmp_path / "expectations.json"
    res = tmp_path / "results.jsonl"
    rec = tmp_path / "records.jsonl"
    records, summary, errors = b08.run_regression(
        expectations_path=exp, results_path=res, records_path=rec,
        freeze=freeze)
    return records, summary, errors, exp, res, rec


def test_two_runs_produce_a_diff(tmp_path):
    """两次回归之间故意改一个期望 → diff 必须报出翻转。"""
    _records, _summary, errors, exp, res, _rec = _run_into_tmp(tmp_path,
                                                                freeze=True)
    assert not errors, f"冻结被拒绝：{errors}"

    b08.run_regression(expectations_path=exp, results_path=res,
                       records_path=tmp_path / "records.jsonl")
    b08.run_regression(expectations_path=exp, results_path=res,
                       records_path=tmp_path / "records.jsonl")

    diff = b08.regression_diff(res)
    assert diff is not None, "两次回归之后应该有 diff"
    assert diff["flipped"] == [], "没改期望时不应该有翻转"

    # 故意改一个期望 → 必须被 diff 抓出来
    frozen = json.loads(exp.read_text(encoding="utf-8"))
    frozen["cases"]["S1_stable_a"]["checks"]["S1-1"] = False
    exp.write_text(json.dumps(frozen, ensure_ascii=False), encoding="utf-8")

    records, summary, _errors = b08.run_regression(
        expectations_path=exp, results_path=res,
        records_path=tmp_path / "records.jsonl")
    diff2 = b08.regression_diff(res)
    flipped = [f for f in diff2["flipped"] if f["case_id"] == "S1_stable_a"]
    assert flipped, "改了期望却没被 diff 抓到"
    assert flipped[0]["from"] == "PASS" and flipped[0]["to"] == "FAIL"
    assert summary["failed"] == 1


def test_cross_version_notice_when_commit_changes(tmp_path):
    """两次回归的 git_commit 不同 → diff 必须标成「跨版本对比」。"""
    res = tmp_path / "results.jsonl"
    rows = [
        {"ts": "2026-10-31T10:00:00", "git_commit": "aaaa111", "cases": [
            {"case_id": "S1_stable_a", "verdict": "PASS"}]},
        {"ts": "2026-10-31T11:00:00", "git_commit": "bbbb222", "cases": [
            {"case_id": "S1_stable_a", "verdict": "PASS"}]},
    ]
    res.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows)
                   + "\n", encoding="utf-8")
    diff = b08.regression_diff(res)
    assert diff["cross_version"] is True
    assert diff["prev"]["git_commit"] == "aaaa111"
    assert diff["cur"]["git_commit"] == "bbbb222"


def test_records_are_appended(tmp_path):
    """记录是追加的：跑两次，行数翻倍。"""
    rec = tmp_path / "records.jsonl"
    exp = tmp_path / "expectations.json"
    res = tmp_path / "results.jsonl"
    b08.run_regression(expectations_path=exp, results_path=res,
                       records_path=rec, freeze=True)
    b08.run_regression(expectations_path=exp, results_path=res,
                       records_path=rec)
    n1 = len(rec.read_text(encoding="utf-8").strip().splitlines())
    b08.run_regression(expectations_path=exp, results_path=res,
                       records_path=rec)
    n2 = len(rec.read_text(encoding="utf-8").strip().splitlines())
    assert n2 == n1 * 2, f"记录不是追加的：{n1} → {n2}"
    rows = [json.loads(x) for x in rec.read_text(encoding="utf-8").splitlines()
            if x.strip()]
    assert len(rows) == n2
    assert all("case_id" in r for r in rows), "记录里必须有 case_id"


def test_results_row_has_the_seven_documented_fields(tmp_path):
    """文档写明的七项 + 版本信息，一个都不能少。"""
    exp = tmp_path / "expectations.json"
    res = tmp_path / "results.jsonl"
    rec = tmp_path / "records.jsonl"
    b08.run_regression(expectations_path=exp, results_path=res,
                       records_path=rec, freeze=True)
    _records, summary, _errors = b08.run_regression(
        expectations_path=exp, results_path=res, records_path=rec)
    for key in ("app_version", "model_version", "verified_at", "total",
                "passed", "failed", "run_ids", "git_commit", "git_dirty",
                "regression_set_id", "negative_control", "cases", "duration_ms"):
        assert key in summary, f"回归汇总行缺字段 {key}"
    nc = summary["negative_control"]
    for key in ("case_id", "expected", "actual", "caught"):
        assert key in nc, f"负对照结果缺字段 {key}"
    assert nc["caught"] is True


def test_no_write_does_not_touch_disk(tmp_path):
    """`--no-write` 必须真的不落盘（记录、汇总、期望清单都不写）。"""
    exp = tmp_path / "e.json"
    res = tmp_path / "r.jsonl"
    rec = tmp_path / "rec.jsonl"
    b08.run_regression(expectations_path=exp, results_path=res,
                       records_path=rec, write=False)
    assert not rec.exists(), "--no-write 却写了回归记录"
    assert not res.exists(), "--no-write 却写了回归汇总"

    b08.run_regression(expectations_path=exp, results_path=res,
                       records_path=rec, freeze=True, write=False)
    assert not exp.exists(), "--no-write 却覆盖了冻结的期望清单"


def test_regression_does_not_create_new_runs(tmp_path):
    """回归不许新跑任何一次（run 存档目录数不变）。"""
    runs_dir = PROJECT_ROOT / "B07" / "runs"
    before = len([p for p in runs_dir.iterdir() if p.is_dir()])
    b08.run_regression(expectations_path=tmp_path / "e.json",
                       results_path=tmp_path / "r.jsonl",
                       records_path=tmp_path / "rec.jsonl", freeze=True)
    after = len([p for p in runs_dir.iterdir() if p.is_dir()])
    assert before == after, "回归跑出了新的 run 存档 —— 回归必须纯离线"
