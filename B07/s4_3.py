"""B07 S4-3 —— 「全部塞进提示词」 vs 「每种证据各有身份」 的对照实验

实验设计（唯一变量 = **证据的组织方式**）：

  两组用**完全相同的四类证据**（同一历史窗口、同一份天气夹具、同一份知识
  检索结果、同一个 B06 模型输出），信息量相同，只有组织方式不同：

  A 组（平铺 / 负面对照）
    - ④ 块放一段**程序从证据台账拼出来的**连续叙述；
    - 不标来源、不标时间、不标状态、不分方向、不提"存在分歧"；
    - **不出现 ⑥ 块，也不出现 ⑦ 块**；
    - ①②③⑤ 块与 B 组逐字节相同。

  B 组（分身份 / 实验组）
    - ④ 块保持现在的结构化判断结果；
    - ⑥ 块：外部天气条件（与 S3 同构）；
    - ⑦ 块：证据分歧（每条带来源与时间）。

  ★ A 组的"综合叙述"由**纯函数** `build_flat_narrative(ledger)` 从台账生成：
    不手写、不让 LLM 生成。否则 A 组就变成"另一个提示词"，实验立刻失去意义。

  ★ 两组之间**只有发给 LLM 的那串上下文**不同：
    anchor / 固定问题 / SYSTEM_PROMPT / B06 模型 / 历史窗口 / 天气夹具 /
    知识检索结果 / ①②③⑤ 块 —— 全部相同，并有指纹表逐项对照。

硬约束：
  - 不改 SYSTEM_PROMPT、不改固定问题、不改判据阈值、不改模型
  - 方向判定仍然是程序规则（复用 B07.evidence_ledger.judge_direction）
  - 断言检测器只用多字短语（B07/s4_3_detector.py）
  - 不许挑样本、不许为了做出差异调参
  - 失败必须显式暴露，并计入分母

用法：
    python -m B07.s4_3            # 每组 3 次
    python -m B07.s4_3 --n 5      # 每组 5 次
"""

from __future__ import annotations

import difflib
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from B07.s4_3_detector import analyze_answer, sources_from_ledger, summarize

STATS_PATH = Path("B07/artifacts/s4_3_contrast.json")

GROUP_A = "A（平铺）"
GROUP_B = "B（分身份）"


# --------------------------------------------------------------------------
# 底座：四类证据只取一次，两组共用
# --------------------------------------------------------------------------

def build_fixtures(anchor: str | None = None) -> dict:
    """构造实验底座：一次取数，两组共用。

    Returns:
        {"anchor", "offline", "ledger", "ledger_data", "bundle", "judgement",
         "base_context", "model_path"}
    """
    from B07.evidence import build_evidence
    from B07.explain import format_judgement_context
    from B07.judgement import build_judgement
    from B07.predict import predict_for_run
    from B07.runs import DEFAULT_MODEL_PATH
    from B07.s4 import RUN_LOCATION, build_ledger
    from B07.s4_offline import build_offline_cooling_context, default_anchor

    anchor = anchor or default_anchor()
    offline = build_offline_cooling_context(anchor)

    anchor_dt = (datetime.fromisoformat(anchor) if isinstance(anchor, str)
                 else anchor)
    evidence = build_evidence(anchor_dt, RUN_LOCATION)
    evidence["scenario"] = "S4"
    bundle = predict_for_run(evidence, DEFAULT_MODEL_PATH)

    ledger = build_ledger(bundle, offline, anchor)
    ledger_data = ledger.to_dict()

    judgement = build_judgement(bundle, evidence_ledger=ledger)
    bundle["judgement"] = judgement
    base_context = format_judgement_context(bundle)

    return {
        "anchor": anchor,
        "offline": offline,
        "ledger": ledger,
        "ledger_data": ledger_data,
        "bundle": bundle,
        "judgement": judgement,
        "base_context": base_context,
        "model_path": DEFAULT_MODEL_PATH,
    }


# --------------------------------------------------------------------------
# A 组的"综合叙述"：纯函数，从台账拼出来
# --------------------------------------------------------------------------

def build_flat_narrative(ledger) -> str:
    """把台账里四类证据的结论拼成**一段连续叙述**（A 组 ④ 块的内容）。

    纯函数：只吃台账，不联网、不调 LLM、不手写具体结论。

    规矩：
      - 顺序固定：内部历史 → 短时预测 → 外部天气 → 相关知识
      - **不出现来源标识**（B02 / B03 / B04 / B06、chunk_id、时间戳、status）
      - **不分方向**（不说哪条支持、哪条相反）
      - **不提"分歧"**
    """
    from B07.evidence_ledger import adopted_ledger_entries

    entries = adopted_ledger_entries(ledger)
    by_type: dict[str, dict] = {}
    for e in entries:
        by_type.setdefault(e.get("source_type"), e)

    parts: list[str] = []

    # 1) 内部历史
    b02 = by_type.get("B02")
    if b02:
        r = b02.get("result") or {}
        concl = str(r.get("stability_conclusion") or "").strip()
        if concl:
            parts.append(f"内部历史显示{concl}")

    # 2) 短时预测
    b06 = by_type.get("B06")
    if b06:
        r = b06.get("result") or {}
        parts.append(
            f"短时预测给出 {r.get('predicted_attention')}"
            f"（置信度 {r.get('confidence')}）"
        )

    # 3) 外部天气
    b03 = by_type.get("B03")
    if b03:
        r = b03.get("result") or {}
        basis = str(r.get("basis") or "").strip()
        sig = r.get("signal")
        parts.append(f"外部天气显示{sig}" + (f"：{basis}" if basis else ""))

    # 4) 相关知识
    b04 = by_type.get("B04")
    if b04:
        r = b04.get("result") or {}
        cites = r.get("citations") or []
        top = cites[0] if cites else {}
        content = str(top.get("content") or "").strip()
        score = top.get("score")
        piece = f"相关知识提示该风险（命中 {len(cites)} 条"
        if score is not None:
            piece += f"，最高分 {round(float(score), 4)}"
        piece += "）"
        if content:
            # 只取条目里"结论性"的一句，避免把整段条文塞进提示词
            head = content.split("；")[0].strip()
            if head:
                piece += f"：{head}"
        parts.append(piece)

    if not parts:
        return "综合当前情况：本次没有可用的证据。"

    return "综合当前情况：" + "；".join(parts) + "。"


#: A 组叙述里**不允许**出现的东西（自动检查用）
FORBIDDEN_IN_A = (
    "B02", "B03", "B04", "B06", "b02", "b03", "b04", "b06",
    "CH-0", "SRC-0", "supporting", "opposing", "undetermined",
    "分歧", "冲突", "证据分歧",
)


def check_narrative_clean(narrative: str) -> list[str]:
    """检查 A 组叙述里没有来源标识 / 方向标签 / 分歧字样。返回问题列表。"""
    problems = []
    for token in FORBIDDEN_IN_A:
        if token in narrative:
            problems.append(f"出现来源/方向标识：{token}")
    # 时间戳形态
    for m in re.finditer(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}", narrative):
        problems.append(f"出现时间戳：{m.group(0)}")
    for w in ("status", "unavailable", "error", "ok"):
        if w in narrative:
            problems.append(f"出现状态词：{w}")
    return problems


# --------------------------------------------------------------------------
# 上下文拼装
# --------------------------------------------------------------------------

BLOCK_RE = re.compile(r"(?=【[①②③④⑤⑥⑦])")


def split_blocks(context: str) -> list[str]:
    """把上下文按 ①~⑦ 块切开。"""
    return [p for p in BLOCK_RE.split(context or "") if p.strip()]


def build_context_a(base_context: str, narrative: str) -> str:
    """由 B 组的基础上下文派生 A 组上下文。

    A 组 = ①②③ + ④A（叙述）+ ⑤，**没有 ⑥、没有 ⑦**。
    ①②③⑤ 直接沿用 base_context 里的原文 → 逐字节相同。
    """
    blocks = split_blocks(base_context)
    out: list[str] = []
    for b in blocks:
        head = b[:4]
        if head.startswith("【④"):
            out.append("【④ 结构化判断结果】\n" + narrative)
        elif head.startswith("【⑥") or head.startswith("【⑦"):
            continue          # A 组没有这两块
        else:
            out.append(b.rstrip("\n"))
    return "\n\n".join(out)


# --------------------------------------------------------------------------
# 指纹
# --------------------------------------------------------------------------

def _sha(text: str) -> str:
    import hashlib
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _sha12(text: str) -> str:
    return _sha(text)[:12]


def fingerprints(fx: dict, ctx_a: str, ctx_b: str) -> dict:
    """两组必须相同的项 + 只该不同的项。"""
    from B07.explain import FIXED_QUESTION, SYSTEM_PROMPT
    from B07.evidence_ledger import adopted_ledger_entries

    ledger = fx["ledger"]
    entries = adopted_ledger_entries(ledger)
    by_type = {e.get("source_type"): e for e in entries}

    def _entry_sha(st):
        e = by_type.get(st)
        if not e:
            return None
        return _sha12(json.dumps(
            {"source": e.get("source"), "time": e.get("time"),
             "status": e.get("status"), "result": e.get("result"),
             "ext": e.get("ext")},
            ensure_ascii=False, sort_keys=True))

    def _blocks(ctx):
        return {b[:2]: b.rstrip("\n") for b in split_blocks(ctx)}

    ba, bb = _blocks(ctx_a), _blocks(ctx_b)
    shared_blocks = {}
    for k in ("【①", "【②", "【③", "【⑤"):
        shared_blocks[k] = (ba.get(k) == bb.get(k))

    return {
        "anchor": fx["anchor"],
        "system_prompt_sha256": _sha12(SYSTEM_PROMPT),
        "question_sha256": _sha12(FIXED_QUESTION),
        "b06_model_version": (fx["bundle"].get("model_output") or {}).get(
            "model_version"),
        "history_window": {
            "start": (fx["bundle"].get("history") or [{}])[0].get("timestamp"),
            "end": (fx["bundle"].get("history") or [{}])[-1].get("timestamp"),
            "count": len(fx["bundle"].get("history") or []),
            "sha256": _sha12(json.dumps(fx["bundle"].get("history"),
                                        ensure_ascii=False, sort_keys=True)),
        },
        "weather_fixture": {
            "signal": (fx["offline"]["context"] or {}).get("signal"),
            "case": fx["offline"].get("case"),
            "sha256": _sha12(json.dumps(fx["offline"]["context"],
                                        ensure_ascii=False, sort_keys=True)),
        },
        "knowledge": {
            "query": ((by_type.get("B04") or {}).get("result") or {}).get("query"),
            "sha256": _entry_sha("B04"),
        },
        "evidence_entries_sha256": {k: _entry_sha(k) for k in
                                    ("B02", "B06", "B03", "B04")},
        "shared_blocks_identical": shared_blocks,
        "context_a_sha256": _sha12(ctx_a),
        "context_b_sha256": _sha12(ctx_b),
        "context_a_len": len(ctx_a),
        "context_b_len": len(ctx_b),
        "shared_prefix_chars": _shared_prefix_len(ctx_a, ctx_b),
    }


def _shared_prefix_len(a: str, b: str) -> int:
    n = min(len(a), len(b))
    i = 0
    while i < n and a[i] == b[i]:
        i += 1
    return i


# --------------------------------------------------------------------------
# 采样
# --------------------------------------------------------------------------

def run_group(group: str, context: str, fx: dict, n: int) -> list[dict]:
    """跑一组 n 次。失败也计入分母。"""
    from B07.runs import run_analysis

    ledger_sources = sources_from_ledger(fx["ledger"])
    records: list[dict] = []
    for i in range(1, n + 1):
        rec = {
            "group": group,
            "seq": i,
            "started_at": datetime.now().isoformat(timespec="seconds"),
            "status": "failed",
            "run_id": None,
            "answer": "",
            "answer_length": 0,
            "usage": {},
            "failure_reason": None,
            "claims_count": 0,
            "cited_count": 0,
            "unsupported_count": 0,
            "ledger_cited_count": 0,
            "attributed_in_text_count": 0,
            "has_claim": False,
            "mentions": {"count": 0, "categories": [], "terms": [],
                         "sentences": []},
            "claims": [],
            "conflict_mentions": {"count": 0, "sentences": []},
        }
        t0 = time.time()
        try:
            archive = run_analysis(
                anchor=fx["anchor"],
                location=(fx["bundle"] or {}).get("location") or "A区-3号温室",
                model_path=fx["model_path"],
                scenario="S4",
                external_conditions=fx["offline"]["context"],
                evidence_ledger=fx["ledger_data"],
                llm_context_override=context,
            )
        except Exception as e:  # noqa: BLE001 —— 失败必须显式暴露
            rec["failure_reason"] = f"{type(e).__name__}: {e}"
            rec["elapsed_s"] = round(time.time() - t0, 1)
            records.append(rec)
            _print_rec(rec)
            continue

        rec["run_id"] = archive.get("run_id")
        rec["status"] = archive.get("status") or "unknown"
        rec["answer"] = archive.get("llm_explanation") or ""
        rec["answer_length"] = len(rec["answer"])
        rec["usage"] = (archive.get("llm_meta") or {}).get("usage") or {}
        rec["llm_context_sha"] = _sha12(archive.get("llm_context") or "")
        rec["llm_context_matches_group"] = (
            (archive.get("llm_context") or "") == context)
        rec["elapsed_s"] = round(time.time() - t0, 1)

        if rec["status"] != "success":
            rec["failure_reason"] = archive.get("failure_reason") or "status != success"
            records.append(rec)
            _print_rec(rec)
            continue

        det = analyze_answer(rec["answer"], context, ledger_sources)
        rec["mentions"] = det["mentions"]
        rec["claims"] = det["claims"]
        rec["claims_count"] = det["claims_count"]
        rec["cited_count"] = det["cited_count"]
        rec["unsupported_count"] = det["unsupported_count"]
        rec["ledger_cited_count"] = det["ledger_cited_count"]
        rec["attributed_in_text_count"] = det["attributed_in_text_count"]
        rec["has_claim"] = det["has_claim"]
        rec["conflict_mentions"] = det["conflict_mentions"]
        records.append(rec)
        _print_rec(rec)
    return records


def _print_rec(rec: dict) -> None:
    u = rec.get("usage") or {}
    print(f"\n  [{rec['group']} #{rec['seq']}] run_id={rec.get('run_id')} "
          f"status={rec.get('status')} 字数={rec.get('answer_length')}")
    print(f"      mentions={rec['mentions'].get('count')} "
          f"类别={rec['mentions'].get('categories')} "
          f"claims={rec.get('claims_count')} "
          f"cited={rec.get('cited_count')} "
          f"unsupported={rec.get('unsupported_count')}")
    print(f"      prompt_tokens={u.get('prompt_tokens')} "
          f"completion_tokens={u.get('completion_tokens')} "
          f"cached_tokens={u.get('cached_tokens')}")
    for c in rec.get("claims") or []:
        print(f"      断言[{c['direction']}] {c['verdict']}: {c['sentence'][:70]}")
    if rec.get("failure_reason"):
        print(f"      失败原因: {rec['failure_reason']}")


# --------------------------------------------------------------------------
# 打印：并排回答
# --------------------------------------------------------------------------

def _w(text: str) -> int:
    """显示宽度（中文按 2 计）。"""
    return sum(2 if ord(ch) > 0x2E7F else 1 for ch in text)


def print_side_by_side(records_a: list[dict], records_b: list[dict]) -> None:
    n = max(len(records_a), len(records_b))
    print("\n" + "=" * 78)
    print("两组回答原文（并排）")
    print("=" * 78)
    for i in range(n):
        a = records_a[i] if i < len(records_a) else None
        b = records_b[i] if i < len(records_b) else None
        print(f"\n---- 第 {i + 1} 次 ----")
        al = (a or {}).get("answer") or f"（{a.get('failure_reason')}）" if a else "（无）"
        bl = (b or {}).get("answer") or f"（{b.get('failure_reason')}）" if b else "（无）"
        a_lines = al.splitlines() or [""]
        b_lines = bl.splitlines() or [""]
        for j in range(max(len(a_lines), len(b_lines))):
            la = a_lines[j] if j < len(a_lines) else ""
            lb = b_lines[j] if j < len(b_lines) else ""
            pad = " " * max(0, 46 - _w(la))
            print(f"{la}{pad}| {lb}")
        print(f"  A: run_id={(a or {}).get('run_id')} "
              f"字数={len(al)}  |  B: run_id={(b or {}).get('run_id')} 字数={len(bl)}")


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def main(n: int = 3, append: bool = False) -> dict:
    from B07.explain import FIXED_QUESTION, SYSTEM_PROMPT

    print("=" * 78)
    print("B07 S4-3 —— 「全部塞进提示词」 vs 「每种证据各有身份」")
    print("=" * 78)

    fx = build_fixtures()
    narrative = build_flat_narrative(fx["ledger"])
    ctx_a = build_context_a(fx["base_context"], narrative)
    ctx_b = fx["base_context"]

    print(f"\nanchor = {fx['anchor']}")
    print(f"SYSTEM_PROMPT sha256 = {_sha12(SYSTEM_PROMPT)}")
    print(f"固定问题    sha256 = {_sha12(FIXED_QUESTION)}")
    print(f"每组采样次数 = {n}")

    # ---- 续跑：已有记录保留，分母只增不减 ----
    prev_a: list[dict] = []
    prev_b: list[dict] = []
    if append and STATS_PATH.exists():
        old = json.loads(STATS_PATH.read_text(encoding="utf-8"))
        prev_a = ((old.get("groups") or {}).get(GROUP_A) or {}).get("records") or []
        prev_b = ((old.get("groups") or {}).get(GROUP_B) or {}).get("records") or []
        print(f"  续跑：已有 A {len(prev_a)} 条、B {len(prev_b)} 条")
    todo_a = max(0, n - len(prev_a))
    todo_b = max(0, n - len(prev_b))

    # ---- A 组叙述（程序生成）----
    print("\n" + "=" * 78)
    print("A 组的『综合叙述』（由 build_flat_narrative 从台账拼出）")
    print("=" * 78)
    print(narrative)
    problems = check_narrative_clean(narrative)
    print(f"\n  自动检查（不得出现来源标识 / 时间戳 / 状态词 / 分歧字样）: "
          f"{'✅ 通过' if not problems else '❌ ' + str(problems)}")

    # ---- 上下文 diff ----
    print("\n" + "=" * 78)
    print("两组上下文 diff（唯一变量 = 证据组织方式）")
    print("=" * 78)
    a_blocks = split_blocks(ctx_a)
    b_blocks = split_blocks(ctx_b)
    print(f"  A 组块：{[b[:2] for b in a_blocks]}")
    print(f"  B 组块：{[b[:2] for b in b_blocks]}")
    print("  逐块一致性：")
    for b in a_blocks:
        key = b[:2]
        same = any(b.rstrip("\n") == x.rstrip("\n") for x in b_blocks)
        print(f"    A 的 {key}  在 B 组中逐字节相同 = {same}")
    for b in b_blocks:
        key = b[:2]
        same = any(b.rstrip("\n") == x.rstrip("\n") for x in a_blocks)
        if not same:
            print(f"    B 的 {key}  在 A 组中**不存在**（A 组少这一块）")
    diff = list(difflib.unified_diff(a_blocks, b_blocks,
                                      fromfile="A（平铺）", tofile="B（分身份）",
                                      lineterm="", n=1))
    print("\n--- unified diff ---")
    print("\n".join(diff))

    fps = fingerprints(fx, ctx_a, ctx_b)

    # ---- 采样 ----
    print("\n" + "=" * 78)
    print(f"▶ A 组（平铺）采样 × {todo_a}")
    print("=" * 78)
    recs_a = list(prev_a) + run_group(GROUP_A, ctx_a, fx, todo_a)

    print("\n" + "=" * 78)
    print(f"▶ B 组（分身份）采样 × {todo_b}")
    print("=" * 78)
    recs_b = list(prev_b) + run_group(GROUP_B, ctx_b, fx, todo_b)

    sum_a = summarize(recs_a)
    sum_b = summarize(recs_b)

    # ---- 指纹对照 ----
    print("\n" + "=" * 78)
    print("指纹对照表（两组必须相同）")
    print("=" * 78)
    print(f"  anchor                 = {fps['anchor']}")
    print(f"  SYSTEM_PROMPT sha256   = {fps['system_prompt_sha256']}")
    print(f"  固定问题 sha256        = {fps['question_sha256']}")
    print(f"  B06 model_version      = {fps['b06_model_version']}")
    print(f"  历史窗口               = {fps['history_window']['start']} → "
          f"{fps['history_window']['end']}（{fps['history_window']['count']} 点）"
          f"  sha={fps['history_window']['sha256']}")
    print(f"  天气夹具               = case={fps['weather_fixture']['case']} "
          f"signal={fps['weather_fixture']['signal']} "
          f"sha={fps['weather_fixture']['sha256']}")
    print(f"  知识检索               = {fps['knowledge']['query']!r} "
          f"sha={fps['knowledge']['sha256']}")
    for k, v in fps["evidence_entries_sha256"].items():
        print(f"  台账证据 {k}  sha256   = {v}")
    for k, same in fps["shared_blocks_identical"].items():
        print(f"  共享块 {k} 逐字节相同 = {same}")
    print(f"  A 上下文 sha256 = {fps['context_a_sha256']}（{fps['context_a_len']} 字）")
    print(f"  B 上下文 sha256 = {fps['context_b_sha256']}（{fps['context_b_len']} 字）")
    print(f"  共享前缀长度 = {fps['shared_prefix_chars']} 字符"
          f"（{fps['shared_prefix_chars'] / max(1, fps['context_a_len']):.1%} of A）")

    # ---- 比率对照 ----
    print("\n" + "=" * 78)
    print("两组比率对照（分母 = 全部尝试，含失败）")
    print("=" * 78)
    rows = [
        ("回答总数（含失败）", "total_answers"),
        ("失败次数", "failed"),
        ("断言总数", "claims_total"),
        ("归因完整率 cited/断言数", "attribution_rate_text"),
        ("无来源断言率 unsupported/断言数", "unsupported_rate_text"),
        ("无断言率 无断言回答/回答数", "no_claim_rate_text"),
        ("台账口径 cited/断言数（辅）", "ledger_cited_rate_text"),
        ("提到来源的回答数", "mentions_any"),
        ("辅助：提到外部天气的回答数", "external_mentions_any"),
        ("辅助：提到分歧/冲突的回答数", "conflict_mentions_any"),
    ]
    print(f"  {'指标':<32} {'A 组（平铺）':<24} {'B 组（分身份）':<24}")
    print("  " + "-" * 78)
    for label, key in rows:
        print(f"  {label:<32} {str(sum_a.get(key)):<24} {str(sum_b.get(key)):<24}")

    # ---- usage 对照 ----
    print("\n" + "=" * 78)
    print("prompt_tokens / cached_tokens 对照")
    print("=" * 78)
    print(f"  {'组':<14} {'#':<4} {'run_id':<10} {'prompt_tokens':<15}"
          f"{'completion_tokens':<19}{'cached_tokens':<15}")
    for tag, recs in (("A", recs_a), ("B", recs_b)):
        for r in recs:
            u = r.get("usage") or {}
            print(f"  {tag:<14} {r.get('seq'):<4} {str(r.get('run_id')):<10} "
                  f"{str(u.get('prompt_tokens')):<15}"
                  f"{str(u.get('completion_tokens')):<19}"
                  f"{str(u.get('cached_tokens')):<15}")
    ca = [ (r.get("usage") or {}).get("cached_tokens") for r in recs_a ]
    cb = [ (r.get("usage") or {}).get("cached_tokens") for r in recs_b ]
    pa = [ (r.get("usage") or {}).get("prompt_tokens") for r in recs_a ]
    pb = [ (r.get("usage") or {}).get("prompt_tokens") for r in recs_b ]
    print(f"\n  A 组 cached_tokens = {ca}")
    print(f"  B 组 cached_tokens = {cb}")
    print(f"  A 组 prompt_tokens = {pa}")
    print(f"  B 组 prompt_tokens = {pb}")
    print(f"  首次调用（A#1 vs B#1）cached 相同 = "
          f"{ca[:1] == cb[:1] if ca and cb else 'N/A'}"
          f"  → 共享前缀逐字节相同（百炼隐式缓存按前缀精确匹配）")

    # ---- 并排回答 ----
    print_side_by_side(recs_a, recs_b)

    # ---- 落盘 ----
    result = {
        "n_per_group": n,
        "anchor": fx["anchor"],
        "fingerprints": fps,
        "narrative_A": narrative,
        "narrative_A_problems": problems,
        "context_A": ctx_a,
        "context_B": ctx_b,
        "groups": {
            GROUP_A: {"summary": sum_a, "records": recs_a},
            GROUP_B: {"summary": sum_b, "records": recs_b},
        },
        "note": ("分母 = 全部尝试次数（含失败）。失败显式记录，不筛选。"
                 "断言检测器词表一次写定，跑完未改。"),
    }
    STATS_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATS_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                          encoding="utf-8")
    print(f"\n已写入 {STATS_PATH}")
    return result


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description="B07 S4-3 对照实验")
    p.add_argument("--n", type=int, default=3, help="每组采样次数")
    p.add_argument("--append", action="store_true",
                   help="续跑：保留已有记录，分母只增不减")
    args = p.parse_args()
    sys.exit(main(args.n, append=args.append))
