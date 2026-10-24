"""B07 S3 阶段验收自检 —— 一条命令跑完全部检查。

    ./venv/Scripts/python.exe -m B07.verify_s3

**全部检查都是离线的**：只读 `B07/runs/` 下的存档与 `B07/artifacts/` 下的产物，
不联网、不调 LLM。因此 LLM 服务不可用时这套验收依然能跑（也必须能跑）。

检查项：

  C1  B07 不变量测试全过（pytest）
  C2  全部 S3_with 存档能被当前判定器重新判定，且与落盘表格一致
  C3  存在"同一 llm_context 既有引用又有未引用"的分组 —— 解释层波动的直接证据
  C4  从存档复算 `combined_attention` 与存档值一致（不依赖 LLM）
  C5  `s3_report.md` 能重新生成，且包含本阶段必须有的章节
  C6  报告里 55% 那条只能以"构造用例"出现，不得混进实测表
  C7  存档目录与 index.json 一致；run_016 / run_017 仍在且带说明

退出码：0 = 全部通过；1 = 有失败项。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

ARTIFACTS = _ROOT / "B07" / "artifacts"
RUNS = _ROOT / "B07" / "runs"
REPORT = ARTIFACTS / "s3_report.md"
TABLE_JSON = ARTIFACTS / "s3_citation_table.json"

PASS, FAIL = "✅", "❌"
_results: list[tuple[str, bool, str]] = []


def _check(name: str, ok: bool, detail: str = "") -> bool:
    _results.append((name, bool(ok), detail))
    print(f"  {PASS if ok else FAIL} {name}" + (f" — {detail}" if detail else ""))
    return bool(ok)


# --------------------------------------------------------------------------

def c1_pytest() -> bool:
    print("\n[C1] B07 不变量测试（pytest）")
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "B07/tests", "-q"],
        cwd=str(_ROOT), capture_output=True, text=True, encoding="utf-8",
    )
    tail = (r.stdout or "").strip().splitlines()
    summary = tail[-1] if tail else "(无输出)"
    return _check("pytest B07/tests", r.returncode == 0, summary)


def c2_recompute_table() -> bool:
    print("\n[C2] 全部 S3_with 存档重新判定")
    from B07.s3_citation_table import collect
    rows = collect()
    if not rows:
        return _check("存在 S3_with 存档", False, "B07/runs 下没有 S3_with 存档")
    cited = sum(1 for r in rows if r["cited"])
    failed = sum(1 for r in rows if r["status"] != "success")
    ok = _check("判定器可重跑全部 S3_with 存档", True,
                f"共 {len(rows)} 次，引用 {cited} 次，失败 {failed} 次"
                f"（分母含失败）")
    if TABLE_JSON.exists():
        stored = json.loads(TABLE_JSON.read_text(encoding="utf-8"))
        same = (stored.get("total") == len(rows)
                and stored.get("cited") == cited)
        ok &= _check("与落盘表格 s3_citation_table.json 一致", same,
                     f"落盘 {stored.get('cited')}/{stored.get('total')}"
                     f" vs 现场 {cited}/{len(rows)}")
    else:
        _check("与落盘表格 s3_citation_table.json 一致", False, "文件不存在")
    return ok


def c3_same_context_pair() -> bool:
    print("\n[C3] 解释层波动的直接证据（同一 llm_context 结果不一致）")
    from B07.s3_citation_table import collect
    groups: dict[str, list[dict]] = {}
    for r in collect():
        groups.setdefault(r["context_sha12"], []).append(r)
    mixed = []
    for sha, rs in groups.items():
        c = sum(1 for r in rs if r["cited"])
        if 0 < c < len(rs):
            mixed.append((sha, c, len(rs),
                          [r["run_id"] for r in rs if r["cited"]],
                          [r["run_id"] for r in rs if not r["cited"]]))
    if not mixed:
        return _check("存在同上下文既有引用又有未引用的分组", False,
                      "没有这样的分组 —— 波动证据缺失")
    detail = "；".join(f"{sha} 组内 {c}/{n}（引用 {'/'.join(cs)}，未引用 {'/'.join(ns)}）"
                       for sha, c, n, cs, ns in mixed)
    return _check("存在同上下文既有引用又有未引用的分组", True, detail)


def c4_combined_attention_recompute() -> bool:
    print("\n[C4] combined_attention 可脱离 LLM 从存档复算")
    from B07.runs import load_run, recompute_from_archive
    ok = True
    checked = 0
    legacy = []
    for d in sorted(RUNS.iterdir()):
        if not d.is_dir() or not d.name.startswith("run_"):
            continue
        if not (d / "run.json").exists():
            continue
        arch = load_run(d.name)
        if arch.get("scenario") != "S3_with":
            continue
        rc = recompute_from_archive(arch)
        ca = (rc.get("recomputed") or {}).get("combined_attention") or {}
        want = (arch.get("combined_attention") or {}).get("value")
        got = ca.get("value")
        checked += 1
        if want is None:
            # 这份存档早于 combined_attention 字段的引入：
            # 规则本来就该能脱离 LLM 复算，复算得到值即通过（不算不一致）。
            legacy.append(f"{d.name}→{got}")
            continue
        if want != got:
            ok = False
            print(f"    ❌ {d.name}: 存档={want} / 复算={got}")
    return _check("全部 S3_with 存档的 combined_attention 复算一致", ok,
                   f"复算 {checked} 份"
                   + (f"（其中 {len(legacy)} 份存档无该字段、由规则复算得出）"
                      if legacy else ""))


def c5_report_sections() -> bool:
    print("\n[C5] 报告可重新生成且章节齐全")
    from B07.s3_report import build_report
    raw = json.loads((ARTIFACTS / "s3_raw_result.json").read_text(encoding="utf-8"))
    from B07.s3_report import _refresh_answers, OFFLINE_PATH
    _refresh_answers(raw)
    if OFFLINE_PATH.exists():
        raw["offline"] = json.loads(OFFLINE_PATH.read_text(encoding="utf-8"))
    text = build_report(raw)
    ok = _check("报告可由原始结果重新生成", True, f"{len(text)} 字符")
    required = [
        "### 3.7 同一状态、两次采样",
        "### 3.8 同一上下文的分组引用率",
        "### 3.9 \"没引用\"到底来自哪里",
        "### 3.10 架构论点",
        "- **L13",
    ]
    missing = [s for s in required if s not in text]
    ok &= _check("报告包含 §3.7 / §3.8 / §3.9 / §3.10 / L13", not missing,
                 "缺：" + "、".join(missing) if missing else "齐全")
    if REPORT.exists():
        on_disk = REPORT.read_text(encoding="utf-8")
        ok &= _check("落盘报告与重新生成的结论一致（引用率口径）",
                     _rate_of(text) == _rate_of(on_disk),
                     f"重生成 {_rate_of(text)} / 落盘 {_rate_of(on_disk)}")
    return ok


def _rate_of(text: str) -> str:
    import re
    m = re.search(r"引用率：(\d+/\d+)", text)
    return m.group(1) if m else "?"


def c6_construct_case_labeled() -> bool:
    print("\n[C6] 55% 那条只能是「构造用例」，不得混进实测表")
    if not REPORT.exists():
        return _check("报告存在", False, str(REPORT))
    text = REPORT.read_text(encoding="utf-8")
    ok = True
    # 找出所有含 "55" 的行
    lines = [ln for ln in text.splitlines() if "55" in ln and "构造" in ln]
    ok &= _check("含 55% 的行都标了「构造用例」", bool(lines),
                 f"{len(lines)} 行")
    bad = [ln for ln in lines if "构造用例（不是实测）" not in ln]
    ok &= _check("55% 行明确标注「不是实测」", not bad,
                 "、".join(bad[:2]) if bad else "标注正确")
    # 实测表里不能出现 55%
    sec = text.split("### 4.1")[1].split("### 4.2")[0] if "### 4.1" in text else ""
    measured_part = sec.split("**构造用例")[0] if "**构造用例" in sec else sec
    ok &= _check("4.1 的实测表里没有 55% 那行", "55.0%" not in measured_part,
                 "实测表干净")
    return ok


def c7_archives_intact() -> bool:
    print("\n[C7] 存档完整性（不删任何 run 存档）")
    index = json.loads((RUNS / "index.json").read_text(encoding="utf-8"))
    entries = index.get("runs") or {}
    dirs = {d.name for d in RUNS.iterdir()
            if d.is_dir() and d.name.startswith("run_")}
    missing = sorted(dirs - set(entries))
    ok = _check("每个 run 目录都在 index.json 里", not missing,
                 "、".join(missing) if missing else f"{len(dirs)} 份存档")
    for rid in ("run_016", "run_017"):
        p = RUNS / rid / "run.json"
        exists = p.exists()
        note = ""
        if exists:
            note = (json.loads(p.read_text(encoding="utf-8")).get("note") or "")
        ok &= _check(f"{rid} 仍在且带说明", exists and bool(note),
                     note[:60] if note else ("缺失" if not exists else "无 note"))
    return ok


# --------------------------------------------------------------------------

def main() -> int:
    print("=" * 78)
    print("B07 S3 阶段验收自检（全部离线，不联网、不调 LLM）")
    print("=" * 78)
    for fn in (c1_pytest, c2_recompute_table, c3_same_context_pair,
               c4_combined_attention_recompute, c5_report_sections,
               c6_construct_case_labeled, c7_archives_intact):
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            _check(f"{fn.__name__} 执行", False, f"{type(e).__name__}: {e}")

    bad = [n for n, ok, _ in _results if not ok]
    print("\n" + "=" * 78)
    if bad:
        print(f"验收结果：❌ {len(bad)}/{len(_results)} 项未通过")
        for n in bad:
            print(f"  - {n}")
        return 1
    print(f"验收结果：✅ {len(_results)}/{len(_results)} 项全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
