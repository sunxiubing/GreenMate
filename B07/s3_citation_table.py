"""任务 A 收口：对**全部** S3_with 存档做统一判定，输出对照表。

用法：
    python -m B07.s3_citation_table          # 打印表 + 写 json

判定口径（与 B07.s3.classify_external_reference 一致）：
    mentions —— 出现外部/天气名词短语或现象（含否定式元陈述）
    claims   —— 不只是"没接入"，而是对外部条件下了事实断言
    cited    —— claims 且句子里带方向词 / 外部数值 / 具体天气现象
    报告里的"引用率"用 **cited**。

分母 = 全部 S3_with 存档数（含失败的）。
"""

from __future__ import annotations

import json
from pathlib import Path

from B07.s3 import (
    ANCHOR,
    SCENARIO_WITH,
    classify_external_reference,
    wide_hint_sentences,
)

TABLE_PATH = Path("B07/artifacts/s3_citation_table.json")


def collect(runs_dir: Path = Path("B07/runs")) -> list[dict]:
    rows = []
    for d in sorted(runs_dir.iterdir()):
        if not d.is_dir() or not d.name.startswith("run_"):
            continue
        p = d / "run.json"
        if not p.exists():
            continue
        arch = json.loads(p.read_text(encoding="utf-8"))
        if arch.get("scenario") != SCENARIO_WITH:
            continue
        answer = arch.get("llm_explanation") or ""
        ext = arch.get("external_conditions")
        cls = classify_external_reference(answer, ext)
        rows.append({
            "run_id": arch.get("run_id"),
            "created_at": arch.get("created_at"),
            "status": arch.get("status"),
            "answer_length": len(answer),
            "mentions": len(cls.get("mentions") or []),
            "claims": len(cls.get("claims") or []),
            "cited": bool(cls.get("cited")),
            "cited_fragments": cls.get("cited_fragments") or [],
            "mention_sentences": cls.get("mentions") or [],
            "claim_sentences": cls.get("claims") or [],
            "meta_only_sentences": cls.get("meta_only_sentences") or [],
            "wide_hint_sentences": wide_hint_sentences(answer),
            "external_signal": (ext or {}).get("signal"),
            "precip_delta": (ext or {}).get("precip_delta"),
            "context_sha12": __import__("hashlib").sha256(
                (arch.get("llm_context") or "").encode("utf-8")).hexdigest()[:12],
            "answer": answer,
        })
    return rows


def main() -> dict:
    rows = collect()
    total = len(rows)
    cited = sum(1 for r in rows if r["cited"])
    failed = sum(1 for r in rows if r["status"] != "success")
    mentions = sum(1 for r in rows if r["mentions"] > 0)

    print("=" * 100)
    print(f"S3_with 全量判定表（anchor={ANCHOR}，分母含失败）")
    print("=" * 100)
    print(f"{'run_id':<10}{'状态':<10}{'字数':>5}{'提及':>5}{'事实':>5}"
          f"{'引用':>6}  {'context_sha':<14} 命中片段")
    for r in rows:
        frag = r["cited_fragments"][0] if r["cited_fragments"] else ""
        print(f"{r['run_id']:<10}{r['status']:<10}{r['answer_length']:>5}"
              f"{r['mentions']:>5}{r['claims']:>5}"
              f"{'是' if r['cited'] else '否':>6}  {r['context_sha12']:<14} {frag}")

    print("-" * 100)
    print(f"总计 {total} 次，引用 {cited} 次 → 引用率 {cited}/{total}"
          f"（失败 {failed} 次计入分母；提及 {mentions} 次）")

    # 同上下文分组：同一 context_sha 内部再看引用率
    by_ctx: dict[str, list[dict]] = {}
    for r in rows:
        by_ctx.setdefault(r["context_sha12"], []).append(r)
    print("\n同一 llm_context 分组（组内引用率 = 采样波动证据）：")
    ctx_summary = []
    for sha, rs in by_ctx.items():
        c = sum(1 for r in rs if r["cited"])
        ctx_summary.append({
            "context_sha12": sha,
            "n": len(rs),
            "cited": c,
            "run_ids": [r["run_id"] for r in rs],
            "precip_delta": rs[0]["precip_delta"],
        })
        print(f"  {sha}  n={len(rs):<3} 引用 {c}/{len(rs)}  "
              f"Δpp={rs[0]['precip_delta']}  {','.join(r['run_id'] for r in rs)}")

    out = {
        "anchor": ANCHOR,
        "total": total,
        "cited": cited,
        "failed": failed,
        "mentions_any": mentions,
        "rate_text": f"{cited}/{total}",
        "rows": rows,
        "context_groups": ctx_summary,
    }
    TABLE_PATH.parent.mkdir(parents=True, exist_ok=True)
    TABLE_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=2),
                          encoding="utf-8")
    print(f"\n已写入 {TABLE_PATH}")
    return out


if __name__ == "__main__":
    main()
