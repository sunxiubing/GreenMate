"""B08 第一遍 · 基线版本快照（文档 p.89「第一步：保存一个 B08 基线版本」）

★ app_version 怎么来的（**可复核**）：

    app_version = "B08_baseline_01"          语义版本号（人工递增）
               + Git 提交指纹                git rev-parse HEAD

  完整形式记在 baseline 的 `app_version_composition.full`：
      B08_baseline_01@<完整 sha>

  ★ **app_version 不写进 run.json** —— run.json 是既有存档契约，
    一个字节都不能改（改了会连带影响视图层的 sha256 与统一检查 U2）。
    文档 p.89 明确允许"如果使用 Git，可以记录对应提交版本"。
    代价见 Limitation L1：历史 run 只能追溯到**当前**基线。

★ 本模块只**读**文件与 git 元数据，不写任何业务文件；
  唯一产物是 B07/artifacts/b08_baseline.json。

用法：
    python -m B07.b08_baseline          # 生成/刷新基线快照
    python -m B07.b08_baseline --show   # 只打印，不写盘
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from B07.explain import FIXED_QUESTION, SYSTEM_PROMPT, _sha12
from B07.scenarios_build import (
    SCENARIO_SPECS,
    SCENARIOS_DIR,
    scenario_digest,
    scenario_path,
)

#: 语义版本号（人工递增；改动内容就换号）
APP_VERSION = "B08_baseline_01"

#: 基线快照输出路径
BASELINE_PATH = _PROJECT_ROOT / "B07" / "artifacts" / "b08_baseline.json"

#: B06 模型产物（manifest + 权重）
MODEL_VERSION = "b06-20261020-v1"
MODEL_MANIFEST = _PROJECT_ROOT / "B06" / "artifacts" / f"{MODEL_VERSION}.json"
MODEL_WEIGHTS = _PROJECT_ROOT / "B06" / "artifacts" / f"{MODEL_VERSION}.joblib"

#: 知识资料清单（相对仓库根）
#:
#: ★ docs/*.md 是设计文档（被知识库 chunk 引用为 page），
#:   B04/SPEC.py 才是**运行时真正被检索的语料**（CHUNKS，TF-IDF）。
#:   两者都算"知识资料版本"，一起记进基线。
KNOWLEDGE_SOURCES = (
    "B04/SPEC.py",
    "docs/b01_b03_design.md",
    "docs/b03_weather.md",
)

RUNS_DIR = _PROJECT_ROOT / "B07" / "runs"


# --------------------------------------------------------------------------
# git
# --------------------------------------------------------------------------

def _git(*args: str) -> str:
    try:
        out = subprocess.run(
            ["git", "-C", str(_PROJECT_ROOT), *args],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=15,
        )
    except Exception as exc:               # noqa: BLE001
        return f"<git 调用失败：{type(exc).__name__}: {exc}>"
    if out.returncode != 0:
        return f"<git {' '.join(args)} 失败：{(out.stderr or '').strip()}>"
    return (out.stdout or "").strip()


def git_state() -> dict:
    """当前 git 状态。**dirty 必须真实反映工作区** —— 脏就是脏。"""
    commit = _git("rev-parse", "HEAD")
    short = _git("rev-parse", "--short", "HEAD")
    branch = _git("rev-parse", "--abbrev-ref", "HEAD")
    porcelain = _git("status", "--porcelain")
    dirty_files = [ln for ln in porcelain.splitlines() if ln.strip()] \
        if porcelain and not porcelain.startswith("<git") else []
    return {
        "commit": commit,
        "commit_short": short,
        "branch": branch,
        "dirty": bool(dirty_files),
        "dirty_files": dirty_files[:80],
        "dirty_file_count": len(dirty_files),
    }


# --------------------------------------------------------------------------
# 指纹
# --------------------------------------------------------------------------

def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def knowledge_state() -> dict:
    """知识资料版本 = 文件 sha256（知识库没有版本号机制，用 sha 代替版本）。"""
    sources = []
    missing = []
    for rel in sorted(KNOWLEDGE_SOURCES):
        p = _PROJECT_ROOT / rel
        if not p.exists():
            missing.append(rel)
            continue
        sources.append({"path": rel, "sha256": _sha256_file(p),
                        "bytes": p.stat().st_size})

    combined = hashlib.sha256()
    for s in sorted(sources, key=lambda x: x["path"]):
        combined.update(s["path"].encode("utf-8"))
        combined.update(b"\n")
        combined.update(s["sha256"].encode("utf-8"))
        combined.update(b"\n")

    return {
        "sources": sources,
        "missing_sources": missing,
        "combined_sha256": combined.hexdigest(),
        "combined_sha256_rule": ("对 sources 按 path 升序排列，逐条把 "
                                 "`<path>\\n<sha256>\\n` 拼进同一个 sha256"),
        "version_note": ("知识库目前**没有版本号机制**，用文件 sha256 代替版本："
                         "文件一改 sha 就变，等价于版本。"),
    }


def model_state() -> dict:
    out = {"version": MODEL_VERSION,
           "manifest_path": str(MODEL_MANIFEST.relative_to(_PROJECT_ROOT)),
           "weights_path": str(MODEL_WEIGHTS.relative_to(_PROJECT_ROOT))}
    if MODEL_MANIFEST.exists():
        out["artifact_manifest_sha256"] = _sha256_file(MODEL_MANIFEST)
    else:
        out["artifact_manifest_sha256"] = None
        out["artifact_manifest_error"] = "文件不存在"
    if MODEL_WEIGHTS.exists():
        out["model_weights_sha256"] = _sha256_file(MODEL_WEIGHTS)
    else:
        out["model_weights_sha256"] = None
        out["model_weights_error"] = "文件不存在"
    return out


def prompt_state() -> dict:
    return {
        "system_prompt_sha256": _sha12(SYSTEM_PROMPT),
        "question_sha256": _sha12(FIXED_QUESTION),
        "question": FIXED_QUESTION,
        "note": ("SYSTEM_PROMPT 与固定问题**绝对不许改**；"
                 "指纹是前 12 位 sha256（与 run.json 的 llm_meta 同口径）。"),
    }


def scenarios_state() -> list[dict]:
    """五个固定场景的摘要（analysis_time / 是否可用 / 指纹）。"""
    out = []
    for sid, spec in SCENARIO_SPECS.items():
        p = scenario_path(sid)
        row = {"scenario_id": sid, "name": spec["name"],
               "analysis_time": None, "available": False,
               "scenario_file": str(p.relative_to(_PROJECT_ROOT)),
               "source_run_id": None, "scenario_sha256": None}
        if p.exists():
            try:
                obj = json.loads(p.read_text(encoding="utf-8"))
            except Exception:               # noqa: BLE001
                obj = {}
            row["analysis_time"] = obj.get("analysis_time")
            row["source_run_id"] = obj.get("source_run_id")
            row["scenario_sha256"] = obj.get("scenario_sha256")
            w = obj.get("weather_evidence_snapshot") or {}
            row["available"] = bool(w.get("available", True))
            row["weather_provider"] = w.get("provider")
            row["weather_is_constructed"] = w.get("is_constructed")
        out.append(row)
    return out


def runs_state() -> dict:
    total = 0
    with_ledger = 0
    ids = []
    for d in sorted(RUNS_DIR.glob("run_*")):
        p = d / "run.json"
        if not p.exists():
            continue
        total += 1
        ids.append(d.name)
        try:
            j = json.loads(p.read_text(encoding="utf-8"))
        except Exception:                   # noqa: BLE001
            continue
        if j.get("evidence_ledger"):
            with_ledger += 1
    return {
        "total": total,
        "with_ledger": with_ledger,
        "without_ledger": total - with_ledger,
        "latest_run_id": ids[-1] if ids else None,
        "note": ("这些 run 是 **B07 开发期产物**，不是 B08 正式验证记录；"
                 "B08 第二遍起的正式验证会另行归档。"),
    }


# --------------------------------------------------------------------------
# 基线组装
# --------------------------------------------------------------------------

def build_baseline() -> dict:
    git = git_state()
    knowledge = knowledge_state()
    model = model_state()
    prompt = prompt_state()
    scenarios = scenarios_state()
    runs = runs_state()

    commit = git.get("commit") or ""
    notes = [
        "app_version 由 **Git 提交指纹**表达，不写进 run.json —— "
        "run.json 是既有存档契约，一个字节都没改。",
        "★ 由此产生 Limitation L1：历史 run **无法追溯到它产生时的 app_version**，"
        "只能追溯到当前基线；B08 第二遍起的正式验证记录会显式带 app_version。",
        "知识资料版本用**文件 sha256**代替版本号（知识库没有版本管理机制）。",
        "固定场景的天气证据是**快照**（反映取得那一刻的外部事实），不是实时天气。",
        "本轮只做第一遍（冻结）。第二遍起（在线运行 / 场景验证 / 同题对照 / "
        "失败验证 / 运行追踪 / 固定回放 / 竞赛表达）**尚未开始**。",
    ]
    if git.get("dirty"):
        notes.append(
            f"★ **工作区是脏的**（{git.get('dirty_file_count')} 个文件有改动或未跟踪）"
            f"—— git.dirty=true。基线对应的提交是 {git.get('commit_short') or '未知'}，"
            f"但**未提交的工作区改动不在这个基线里**，"
            f"所以它不是一份干净基线；提交后再跑一次本命令刷新快照。")
    else:
        notes.append("工作区干净（git.dirty=false），基线对应一份可复现的提交。")

    return {
        "app_version": APP_VERSION,
        "app_version_composition": {
            "rule": "app_version = 语义版本号 + Git 提交指纹",
            "app_version": APP_VERSION,
            "git_commit": commit,
            "git_commit_short": git.get("commit_short"),
            "full": f"{APP_VERSION}@{commit}" if commit else APP_VERSION,
            "note": ("app_version **不进 run.json**（存档结构一字不改）；"
                     "它表达的是「冻结时这份代码是哪一版」。"),
        },
        "frozen_at": datetime.now().isoformat(timespec="seconds"),
        "git": git,
        "model": model,
        "knowledge": knowledge,
        "prompt": prompt,
        "scenarios": scenarios,
        "runs_on_disk": runs,
        "notes": notes,
    }


def write_baseline(obj: dict) -> Path:
    BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
    BASELINE_PATH.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")
    return BASELINE_PATH


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="B08 第一遍 · 基线版本快照")
    ap.add_argument("--show", action="store_true", help="只打印，不写盘")
    args = ap.parse_args(argv)

    obj = build_baseline()
    if not args.show:
        write_baseline(obj)

    print("=" * 74)
    print("B08 第一遍 · 基线版本快照" + ("（--show，未写盘）" if args.show else ""))
    print("=" * 74)
    g = obj["git"]
    print(f"\n  app_version   : {obj['app_version']}")
    print(f"  完整形式      : {obj['app_version_composition']['full']}")
    print(f"  git 分支      : {g.get('branch')}   commit={g.get('commit_short')}")
    print(f"  工作区 dirty  : {g.get('dirty')}"
          f"（{g.get('dirty_file_count')} 个文件）")
    m = obj["model"]
    print(f"\n  模型版本      : {m.get('version')}")
    print(f"    manifest    : {m.get('artifact_manifest_sha256')}")
    print(f"    weights     : {m.get('model_weights_sha256')}")
    k = obj["knowledge"]
    print(f"\n  知识版本      : combined_sha256={k.get('combined_sha256')}")
    for s in k.get("sources", []):
        print(f"    {s['path']:<28} {s['sha256'][:16]}…")
    pr = obj["prompt"]
    print(f"\n  提示词指纹    : {pr['system_prompt_sha256']}")
    print(f"  固定问题指纹  : {pr['question_sha256']}")
    print(f"  固定问题      : {pr['question']}")
    print("\n  五个固定场景：")
    for s in obj["scenarios"]:
        print(f"    {s['scenario_id']} {s['name']:<8} available={s['available']}"
              f"  来源={s['source_run_id']}  sha={str(s['scenario_sha256'])[:16]}…")
    r = obj["runs_on_disk"]
    print(f"\n  磁盘 run      : {r['total']} 个（带台账 {r['with_ledger']}）")
    print("\n  提示：")
    for n in obj["notes"]:
        print(f"    · {n}")
    if not args.show:
        print(f"\n  已写入：{BASELINE_PATH.relative_to(_PROJECT_ROOT)}")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
