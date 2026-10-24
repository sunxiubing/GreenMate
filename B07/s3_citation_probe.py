"""任务 A：LLM 到底会不会引用外部条件 —— 可重复性探针。

实验设计（唯一变量 = LLM 采样）：

  * 同一 anchor（2026-10-21T16:00:00）、同一 B02 内部历史、同一 B06 模型、
    同一 SYSTEM_PROMPT、同一固定问题 —— 这些在整组内都不变。
  * 外部上下文（LLM 上下文里的 ⑥ 块）在整组内**冻结**：
    build_external_context 只调一次，之后每次 run 复用同一份（deepcopy 后传入，
    避免被下游改写）。于是整组的 ``llm_context`` 逐字节相同，
    组内唯一变化的就是 LLM 的采样。
  * 请求**没有**显式设置 temperature，用的是服务端默认值 —— 所以重复请求
    本身就会产生波动，这正是要测的东西。

记账规则（防止选择偏差）：

  * 每跑必打印：run_id / 字数 / 是否引用 / 命中原文片段。
  * **失败或异常中断的 run 一律计入分母**，并原样记录失败原因；
    不允许只保留成功的那些。
  * 存档本身由 B07.runs 落盘（失败也落盘，status="failed"），事后可复算。

产物：``B07/artifacts/s3_citation_stats.json``
"""

from __future__ import annotations

import copy
import json
import time
from datetime import datetime
from pathlib import Path

from B07.explain import FIXED_QUESTION, MAX_ANSWER_CHARS, SYSTEM_PROMPT, _sha12
from B07.s3 import (
    ANCHOR,
    DEFAULT_LOCATION,
    SCENARIO_WITH,
    WEATHER_LOCATION,
    WEATHER_WINDOW,
    classify_external_reference,
    wide_hint_sentences,
)

STATS_PATH = Path("B07/artifacts/s3_citation_stats.json")
# 冻结的 ⑥ 块落盘：保证"同一状态"跨多次调用也成立（真实天气是活的，
# 不冻结的话每次调用 ⑥ 块都会变，测出来的就是"天气漂移"而不是"采样波动"）
FROZEN_EXT_PATH = Path("B07/artifacts/s3_frozen_ext.json")

# 组一：⑥ 块冻结（同一状态，纯采样方差）
# 组二：每次重新取数（贴近真实链路，但 ⑥ 块会随真实天气漂移）
GROUP_FROZEN = "frozen"
GROUP_LIVE = "live"


# --------------------------------------------------------------------------
# 单次 run 的记账
# --------------------------------------------------------------------------

def _judge_answer(answer: str, ext: dict | None) -> dict:
    """用扩充后的三级检测器判定一次回答。

    mentions = 出现了外部名词/现象
    claims   = 不只是"没接入"这种元陈述，而是有事实断言
    cited    = 事实断言 + 方向词/数值/现象词（报告里的"引用"口径 = cited）
    """
    return classify_external_reference(answer or "", ext)


def _run_one(ext: dict, location: str, model_path, group: str, idx: int) -> dict:
    """跑一次 S3_with 并记账。**绝不吞异常**：失败也要返回一条记录进分母。"""
    from B07.runs import run_analysis

    rec = {
        "group": group,
        "seq": idx,
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "status": "failed",
        "run_id": None,
        "answer_length": 0,
        "mentions": 0,
        "claims": 0,
        "cited": False,
        "cited_fragments": [],
        "llm_context_sha": None,
        "failure_reason": None,
    }
    t0 = time.time()
    try:
        archive = run_analysis(
            ANCHOR, location, model_path,
            scenario=SCENARIO_WITH,
            external_conditions=copy.deepcopy(ext),
        )
    except Exception as e:  # noqa: BLE001 —— 失败必须显式暴露，不许只留成功的
        rec["failure_reason"] = f"{type(e).__name__}: {e}"
        rec["elapsed_s"] = round(time.time() - t0, 1)
        return rec

    rec["run_id"] = archive.get("run_id")
    rec["status"] = archive.get("status") or "unknown"
    answer = archive.get("llm_explanation") or ""
    rec["answer_length"] = len(answer)
    rec["llm_context_sha"] = _sha12(archive.get("llm_context") or "")
    rec["combined_attention"] = (archive.get("combined_attention") or {}).get("value")
    rec["external_signal"] = (archive.get("external_conditions") or {}).get("signal")

    if rec["status"] != "success":
        rec["failure_reason"] = archive.get("failure_reason") or "status != success"
        rec["elapsed_s"] = round(time.time() - t0, 1)
        return rec

    cls = _judge_answer(answer, ext)
    rec["mentions"] = len(cls.get("mentions") or [])
    rec["claims"] = len(cls.get("claims") or [])
    rec["cited"] = bool(cls.get("cited"))
    rec["cited_fragments"] = cls.get("cited_fragments") or []
    rec["mention_sentences"] = cls.get("mentions") or []
    rec["claim_sentences"] = cls.get("claims") or []
    rec["meta_only_sentences"] = cls.get("meta_only_sentences") or []
    rec["wide_hint_sentences"] = wide_hint_sentences(answer)
    rec["answer"] = answer
    rec["elapsed_s"] = round(time.time() - t0, 1)
    return rec


# --------------------------------------------------------------------------
# 整组
# --------------------------------------------------------------------------

def run_group(group: str, n: int, ext: dict, location: str = DEFAULT_LOCATION,
              model_path=None, progress=None) -> list[dict]:
    records = []
    for i in range(1, n + 1):
        rec = _run_one(ext, location, model_path, group, i)
        records.append(rec)
        _print_rec(rec)
    return records


def _print_rec(rec: dict) -> None:
    tag = "引用" if rec.get("cited") else "未引用"
    if rec.get("status") != "success":
        tag = "失败（计入分母）"
    print(f"  [{rec['group']} #{rec['seq']}] run_id={rec.get('run_id')} "
          f"status={rec.get('status')} 字数={rec.get('answer_length')} "
          f"是否引用={tag}")
    if rec.get("cited_fragments"):
        for frag in rec["cited_fragments"]:
            print(f"      命中片段: {frag}")
    if rec.get("claim_sentences"):
        for s in rec["claim_sentences"]:
            print(f"      事实句  : {s}")
    if rec.get("meta_only_sentences"):
        for s in rec["meta_only_sentences"]:
            print(f"      元陈述句: {s}")
    if rec.get("failure_reason"):
        print(f"      失败原因: {rec['failure_reason']}")
    if rec.get("wide_hint_sentences"):
        for s in rec["wide_hint_sentences"]:
            print(f"      兜底扫描: {s}")


# --------------------------------------------------------------------------
# 统计
# --------------------------------------------------------------------------

def summarize(records: list[dict]) -> dict:
    """引用率 = 引用次数 / 总次数。**分母是全部尝试**，含失败。"""
    total = len(records)
    cited = sum(1 for r in records if r.get("cited"))
    failed = sum(1 for r in records if r.get("status") != "success")
    mentions = sum(1 for r in records if r.get("mentions", 0) > 0)
    claims = sum(1 for r in records if r.get("claims", 0) > 0)
    return {
        "total": total,
        "cited": cited,
        "not_cited": total - cited,
        "failed": failed,
        "mentions_any": mentions,
        "claims_any": claims,
        "citation_rate": (round(cited / total, 4) if total else None),
        "rate_text": f"{cited}/{total}" if total else "0/0",
    }


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def _load_or_create_frozen_ext(anchor: datetime,
                               prefer_run_id: str | None = None) -> dict:
    """取（或首次创建并落盘）冻结的外部上下文。

    优先级：
      1. ``B07/artifacts/s3_frozen_ext.json`` 已存在 → 直接沿用；
      2. 已有冻结组存档（prefer_run_id）→ 从该存档的 external_conditions 还原
         （存档里存的就是当时喂给 LLM 的 ⑥ 块，逐字一致）；
      3. 都没有 → 现场取一次并落盘。

    这样"同一 anchor、同一状态"在跨进程调用下也成立。
    """
    if FROZEN_EXT_PATH.exists():
        ext = json.loads(FROZEN_EXT_PATH.read_text(encoding="utf-8"))
        print(f"  沿用已冻结的 ⑥ 块（{FROZEN_EXT_PATH}）")
        return ext

    if prefer_run_id:
        from B07.runs import run_json_path
        p = run_json_path(prefer_run_id)
        if p and Path(p).exists():
            arch = json.loads(Path(p).read_text(encoding="utf-8"))
            ext = arch.get("external_conditions")
            if ext:
                FROZEN_EXT_PATH.parent.mkdir(parents=True, exist_ok=True)
                FROZEN_EXT_PATH.write_text(
                    json.dumps(ext, ensure_ascii=False, indent=2), encoding="utf-8")
                print(f"  从存档 {prefer_run_id} 还原冻结 ⑥ 块 → {FROZEN_EXT_PATH}")
                return ext

    from B07.weather import build_external_context
    ext = build_external_context(anchor, WEATHER_LOCATION, WEATHER_WINDOW)
    FROZEN_EXT_PATH.parent.mkdir(parents=True, exist_ok=True)
    FROZEN_EXT_PATH.write_text(json.dumps(ext, ensure_ascii=False, indent=2),
                               encoding="utf-8")
    print(f"  已创建并冻结 ⑥ 块 → {FROZEN_EXT_PATH}")
    return ext


def main(n_frozen: int = 8, n_live: int = 4, append: bool = False) -> dict:
    from B07.runs import DEFAULT_MODEL_PATH
    from B07.weather import build_external_context

    anchor = datetime.fromisoformat(ANCHOR)

    print("\n" + "=" * 78)
    print("任务 A · 引用率探针")
    print(f"anchor={ANCHOR}  模型={DEFAULT_MODEL_PATH}")
    print(f"system_prompt_sha256={_sha12(SYSTEM_PROMPT)}  "
          f"question_sha256={_sha12(FIXED_QUESTION)}  "
          f"MAX_ANSWER_CHARS={MAX_ANSWER_CHARS}")
    print("=" * 78)

    # ---- 续跑：已有记录不重跑、不丢弃（分母只增不减）----
    prev = {"frozen": [], "live": []}
    if append and STATS_PATH.exists():
        old = json.loads(STATS_PATH.read_text(encoding="utf-8"))
        for g in ("frozen", "live"):
            prev[g] = (old.get("groups", {}).get(g, {}) or {}).get("records") or []
        print(f"  续跑：已有 frozen {len(prev['frozen'])} 条、"
              f"live {len(prev['live'])} 条")

    # ---- 组一：⑥ 块冻结 ----
    print("\n▶ 组一：外部上下文冻结（同一状态，只测采样波动）")
    prefer = None
    if prev["frozen"]:
        prefer = next((r.get("run_id") for r in prev["frozen"]
                       if r.get("run_id")), None)
    frozen_ext = _load_or_create_frozen_ext(anchor, prefer_run_id=prefer)
    print(f"  ⑥ 块 signal={frozen_ext.get('signal')} "
          f"judge_method={frozen_ext.get('judge_method')} "
          f"precip_delta={frozen_ext.get('precip_delta')}")

    to_frozen = max(0, n_frozen - len(prev["frozen"]))
    to_live = max(0, n_live - len(prev["live"]))

    frozen_records = list(prev["frozen"])
    frozen_records += run_group(GROUP_FROZEN, to_frozen, frozen_ext,
                                model_path=DEFAULT_MODEL_PATH)

    # ---- 组二：每次重新取数 ----
    print("\n▶ 组二：每次重新取真实天气（贴近真实链路，⑥ 块会漂移）")
    live_records = list(prev["live"])
    for i in range(len(live_records) + 1, len(live_records) + to_live + 1):
        ext = build_external_context(anchor, WEATHER_LOCATION, WEATHER_WINDOW)
        print(f"  · 第 {i} 次取数 signal={ext.get('signal')} "
              f"precip_delta={ext.get('precip_delta')}")
        rec = _run_one(ext, DEFAULT_LOCATION, DEFAULT_MODEL_PATH, GROUP_LIVE, i)
        live_records.append(rec)
        _print_rec(rec)

    all_records = frozen_records + live_records
    frozen_sum = summarize(frozen_records)
    live_sum = summarize(live_records)
    total_sum = summarize(all_records)

    # ---- 上下文一致性自证（分组看：冻结组必须逐字节相同）----
    frozen_shas = sorted({r["llm_context_sha"] for r in frozen_records
                          if r.get("llm_context_sha")})
    live_shas = sorted({r["llm_context_sha"] for r in live_records
                         if r.get("llm_context_sha")})
    all_shas = sorted(set(frozen_shas) | set(live_shas))
    context_identical = len(frozen_shas) == 1

    result = {
        "anchor": ANCHOR,
        "system_prompt_sha256": _sha12(SYSTEM_PROMPT),
        "question_sha256": _sha12(FIXED_QUESTION),
        "max_answer_chars": MAX_ANSWER_CHARS,
        "temperature_pinned": False,
        "temperature_note": ("LLM 请求未显式设置 temperature，使用服务端默认值"
                             "（具体数值未核实）"),
        "frozen_ext": {k: frozen_ext.get(k) for k in
                       ("signal", "judge_method", "precip_delta", "temp_delta",
                        "cloud_delta", "available", "forecast_window",
                        "location", "analysis_time")},
        "groups": {
            GROUP_FROZEN: {"summary": frozen_sum, "records": frozen_records},
            GROUP_LIVE: {"summary": live_sum, "records": live_records},
        },
        "overall": total_sum,
        "llm_context_sha_set_frozen": frozen_shas,
        "llm_context_sha_set_live": live_shas,
        "llm_context_sha_set_all": all_shas,
        "llm_context_identical_within_frozen_group": context_identical,
        "note": ("分母 = 全部尝试次数（含失败/异常）。"
                 "失败 run 一律计入，不做筛选。"),
    }

    STATS_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATS_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                          encoding="utf-8")

    print("\n" + "=" * 78)
    print("引用率统计（分母含失败）")
    print("=" * 78)
    for name, s in (("组一 ⑥ 冻结", frozen_sum),
                    ("组二 实时取数", live_sum),
                    ("合计", total_sum)):
        print(f"  {name}: 引用 {s['rate_text']}  "
              f"(失败 {s['failed']}，提及 {s['mentions_any']}，"
              f"事实断言 {s['claims_any']})")
    print(f"\n  冻结组 llm_context 逐字节相同: {context_identical}")
    print(f"  冻结组 sha 集合: {frozen_shas}")
    print(f"  实时组 sha 集合: {live_shas}")
    print(f"\n  已写入 {STATS_PATH}")
    return result


if __name__ == "__main__":
    main()
