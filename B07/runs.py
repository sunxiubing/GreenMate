"""B07 Step 1（第五步）— 运行存档 + 事后可回溯

职责：
  1. 为一次完整分析生成 run_id（run_001 顺序编号），把 S1-1~S1-4 的产物拼成一份快照落盘
  2. 维护 B07/runs/index.json 索引
  3. explain_run(run_id) 完全从存档文件读取并**重新算一遍**事实性内容后回答

存档结构（文件 + 索引，不引数据库/ORM）：
  B07/runs/index.json            索引：run_id → {created_at, question, verdict, model_version, status}
  B07/runs/<run_id>/run.json     单次运行的完整快照

关键设计：
  - explain_run 不依赖任何内存状态：进程退出后另起进程照样能读
  - "看到了什么"这类事实性内容从存档里的原始 6 点重新算（judge_stability 重跑），
    不是把当初的文本复读一遍
  - LLM 解释本身是历史记录，原样展示；但它不参与事实复算
  - 失败也要留痕：任一环节失败，落一份 status="failed" 的 run.json，含 stage 与原因
  - 存档里不写 API Key 等敏感信息（落盘前有断言扫描）

用法：
  python -m B07.runs run                          # 跑一次分析并存档
  python -m B07.runs list                         # 列出所有 run
  python -m B07.runs explain run_001              # 回溯某次运行
"""

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from B07.config import DEFAULT_LOCATION

# --------------------------------------------------------------------------
# 路径与常量
# --------------------------------------------------------------------------
RUNS_DIR = _PROJECT_ROOT / "B07" / "runs"
INDEX_PATH = RUNS_DIR / "index.json"

RUN_ID_PREFIX = "run_"
RUN_ID_PAD = 3                 # run_001 / run_002 ...
INDEX_VERSION = 1

DEFAULT_MODEL_PATH = _PROJECT_ROOT / "B06" / "artifacts" / "b06-20261020-v1"

# 回溯时回答的问题（与 S1-4B 的固定问题一致）
REPLAY_QUESTION = "这次 GreenMate 看到了什么，最后为什么说它比较稳定？"

# 存档里禁止出现的敏感字段名（落盘前扫描）
FORBIDDEN_KEY_HINTS = ("api_key", "apikey", "secret", "token", "password", "passwd", "credential")


class RunError(RuntimeError):
    """存档/回溯环节失败。"""


# --------------------------------------------------------------------------
# 索引读写
# --------------------------------------------------------------------------

def _empty_index() -> dict:
    return {
        "version": INDEX_VERSION,
        "next_seq": 1,
        "runs": {},
    }


def _load_index() -> dict:
    """读索引。文件不存在或损坏时重建（损坏会备份成 .corrupt）。"""
    if not INDEX_PATH.exists():
        return _empty_index()
    try:
        with open(INDEX_PATH, encoding="utf-8") as f:
            idx = json.load(f)
    except json.JSONDecodeError:
        backup = INDEX_PATH.with_suffix(".json.corrupt")
        INDEX_PATH.replace(backup)
        print(f"  ⚠️  index.json 解析失败，已备份为 {backup.name}，重建空索引")
        return _empty_index()

    idx.setdefault("version", INDEX_VERSION)
    idx.setdefault("next_seq", 1)
    idx.setdefault("runs", {})
    return idx


def _save_index(index: dict) -> None:
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    _atomic_write_json(INDEX_PATH, index)


def _reserve_run_id() -> str:
    """分配下一个可用编号，并立即把 next_seq 落盘（避免 id 被重复使用）。"""
    index = _load_index()
    seq = int(index.get("next_seq", 1))
    run_id = f"{RUN_ID_PREFIX}{seq:0{RUN_ID_PAD}d}"
    index["next_seq"] = seq + 1
    _save_index(index)
    return run_id


def _atomic_write_json(path: Path, obj) -> None:
    """先写临时文件再 rename，避免半截文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


# --------------------------------------------------------------------------
# 敏感信息防护
# --------------------------------------------------------------------------

def assert_no_secrets(obj, path: str = "$") -> None:
    """递归扫描存档内容，发现疑似密钥字段直接报错。

    不是摆设——LLM 相关模块很容易顺手把整个 env 或 client 配置塞进结果里。
    """
    if isinstance(obj, dict):
        for k, v in obj.items():
            kl = str(k).lower()
            if any(h in kl for h in FORBIDDEN_KEY_HINTS):
                raise RunError(f"存档内容包含疑似敏感字段，已中止落盘：{path}.{k}")
            assert_no_secrets(v, f"{path}.{k}")
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            assert_no_secrets(v, f"{path}[{i}]")
    elif isinstance(obj, str):
        # 常见 key 前缀形态
        if "sk-" in obj and len(obj) > 20:
            raise RunError(f"存档内容疑似包含 API Key（{path}），已中止落盘")


# --------------------------------------------------------------------------
# 存档
# --------------------------------------------------------------------------

def run_dir(run_id: str) -> Path:
    return RUNS_DIR / run_id


def run_json_path(run_id: str) -> Path:
    return run_dir(run_id) / "run.json"


def load_run(run_id: str) -> dict:
    """从磁盘读一份 run 快照。"""
    p = run_json_path(run_id)
    if not p.exists():
        raise RunError(f"找不到存档：{p}（可用 `python -m B07.runs list` 查看现有 run）")
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def save_run(archive: dict) -> Path:
    """落盘 run.json + 更新 index.json。"""
    run_id = archive["run_id"]
    assert_no_secrets(archive)

    path = run_json_path(run_id)
    _atomic_write_json(path, archive)

    index = _load_index()
    try:
        rel_path = str(path.relative_to(_PROJECT_ROOT)).replace("\\", "/")
    except ValueError:
        # 存档目录不在项目根之下（例如测试用临时目录），记绝对路径
        rel_path = str(path).replace("\\", "/")

    index["runs"][run_id] = {
        "created_at": archive.get("created_at"),
        "question": archive.get("question"),
        "verdict": (archive.get("structured_judgement") or {}).get("predicted_attention")
                   or (archive.get("model_output") or {}).get("predicted_attention"),
        "model_version": (archive.get("model_output") or {}).get("model_version"),
        "status": archive.get("status"),
        "failed_stage": archive.get("failed_stage"),
        "path": rel_path,
    }
    _save_index(index)
    return path


def list_runs() -> list[dict]:
    """列出所有 run（按编号排序）。"""
    index = _load_index()
    out = []
    for run_id in sorted(index["runs"].keys()):
        item = dict(index["runs"][run_id])
        item["run_id"] = run_id
        out.append(item)
    return out


# --------------------------------------------------------------------------
# 跑一次完整分析并存档
# --------------------------------------------------------------------------

def _provider_name() -> str:
    """从 LLM_BASE_URL 取主机名当 provider。只存主机名，不存 key。"""
    base = os.environ.get("LLM_BASE_URL", "")
    host = base.split("//")[-1].split("/")[0]
    return host or "unknown"


def run_analysis(anchor: datetime | str, location: str = DEFAULT_LOCATION,
                 model_path: str | Path = DEFAULT_MODEL_PATH,
                 window_selection: dict | None = None,
                 progress=None) -> dict:
    """执行一次完整分析（S1-2 → S1-3 → S1-4），把产物拼成快照落盘。

    Args:
        anchor:           时间锚点
        location:         位置标识
        model_path:       B06 已存模型产物路径
        window_selection: S1-1 选窗结果，原样存进存档（可选）
        progress:         阶段回调 progress(stage, extra)，供上层打印进度（可选）

    Returns: 存档 dict（已落盘）

    任一环节失败：
      - 先落一份 status="failed" 的 run.json（含 failed_stage / failure_reason）
      - 再把异常抛出（调用方应感知失败）
    """
    from B07.evidence import build_evidence
    from B07.predict import predict_for_run
    from B07.judgement import build_judgement, self_check
    from B07.explain import (
        FIXED_QUESTION, explain_judgement, compare_judgement_vs_explanation,
    )
    from B07.stability import judge_stability

    if isinstance(anchor, str):
        anchor = datetime.fromisoformat(anchor)

    run_id = _reserve_run_id()
    created_at = datetime.now().isoformat(timespec="seconds")

    archive: dict = {
        "run_id": run_id,
        "created_at": created_at,
        "status": "running",
        "failed_stage": None,
        "failure_reason": None,
        "question": FIXED_QUESTION,
        "analysis_time": anchor.isoformat(),
        "location": location,
        "model_path": str(model_path),
        # 下面这些字段先占位，保证成功/失败的存档结构一致
        "current_state": None,
        "latest_in_db": None,
        "hours_gap": None,
        "history": [],
        "history_window": None,
        "model_output": None,
        "model_meta": {},
        "structured_judgement": None,
        "judgement_self_check": None,
        "llm_explanation": None,
        "llm_meta": {},
        "evidence_sources": {},
        "warnings": [],
        "window_selection": window_selection,
    }

    def _notify(stage_name: str, extra=None):
        if progress is not None:
            try:
                progress(stage_name, extra)
            except Exception:
                pass  # 进度回调出问题不能影响主流程

    stage = "evidence"
    try:
        # ---------------- S1-2 证据 ----------------
        evidence = build_evidence(anchor, location)
        archive["current_state"] = evidence["current_state"]
        archive["latest_in_db"] = evidence["latest_in_db"]
        archive["hours_gap"] = evidence["hours_gap"]
        archive["history"] = evidence["history"]          # 原始 6 点，复算要用

        # 历史窗口 + 稳定判定（存档一次，回溯时再算一次对照）
        samples = _samples_from_history(evidence["history"])
        judged = judge_stability(samples)
        archive["history_window"] = {
            "start": evidence["history_window"]["start"],
            "end": evidence["history_window"]["end"],
            "count": evidence["history_window"]["count"],
            "stability": {
                "passed": judged["passed"],
                "criteria": judged["criteria"],
                "conclusion": judged["conclusion"],
            },
        }

        _notify("evidence", {
            "current_state": archive["current_state"],
            "history_count": archive["history_window"]["count"],
            "hours_gap": archive["hours_gap"],
            "stability": archive["history_window"]["stability"],
        })

        # ---------------- S1-3 预测 ----------------
        stage = "predict"
        bundle = predict_for_run(evidence, model_path)
        mo = bundle["model_output"]
        archive["model_output"] = {
            "evidence_type": mo["evidence_type"],
            "window_id": mo["window_id"],
            "predicted_attention": mo["predicted_attention"],
            "confidence": mo["confidence"],
            "confidence_normal": mo["confidence_normal"],
            "confidence_watch": mo["confidence_watch"],
            "model_version": mo["model_version"],
            "dtw_temperature": mo["dtw_temperature"],
            "dtw_humidity": mo["dtw_humidity"],
            "dtw_lux": mo["dtw_lux"],
            "feature_count": mo["feature_count"],
            "feature_contribution_method": mo["feature_contribution_method"],
            "coef_top5": mo["feature_contributions_top5"],
            "stability_baseline": mo.get("stability_baseline"),
            "errors": mo["errors"],
        }
        archive["model_meta"] = bundle.get("model_meta") or {}

        _notify("predict", {
            "predicted_attention": mo["predicted_attention"],
            "confidence": mo["confidence"],
            "model_version": mo["model_version"],
            "errors": mo["errors"],
        })

        # ---------------- S1-4A 结构化判断 ----------------
        stage = "judgement"
        judgement = build_judgement(bundle)
        bundle["judgement"] = judgement
        archive["structured_judgement"] = judgement
        archive["judgement_self_check"] = self_check(bundle)

        _notify("judgement", {
            "judgement": judgement,
            "self_check": archive["judgement_self_check"],
        })

        # ---------------- S1-4B LLM 解释 ----------------
        stage = "llm_explanation"
        t0 = time.time()
        explanation = explain_judgement(bundle, judgement)
        elapsed_ms = int((time.time() - t0) * 1000)

        archive["llm_explanation"] = explanation["answer"]
        archive["llm_meta"] = {
            "provider": _provider_name(),
            "model": explanation["model"],
            "elapsed_ms": elapsed_ms,
            "content_length": explanation["content_length"],
            "question": explanation["question"],
        }

        _notify("llm_explanation", {
            "answer": explanation["answer"],
            "llm_meta": archive["llm_meta"],
        })

        # ---------------- 一致性校验（只记录） ----------------
        stage = "consistency_check"
        warnings = list(bundle.get("warnings") or [])
        warnings += compare_judgement_vs_explanation(judgement, explanation, bundle)
        archive["warnings"] = warnings

        _notify("consistency_check", {"warnings": warnings})

        archive["evidence_sources"] = {
            "B02": "environment_samples（B02.api.history.get_environment_history，显式起止时间）",
            "B05": "get_current_environment（B05.test_functions，记录为 latest_in_db，仅参考）",
            "B06": (f"模型 {archive['model_output']['model_version']}"
                    f"（标签规则 {archive['model_meta'].get('label_source')}），只加载不重训"),
            "B01": (f"LLM {archive['llm_meta']['model']}"
                    f"（经 B01.llm.get_client，OpenAI 兼容接口）"),
        }

        archive["status"] = "success"

    except Exception as e:
        archive["status"] = "failed"
        archive["failed_stage"] = stage
        archive["failure_reason"] = f"{type(e).__name__}: {e}"
        archive.setdefault("warnings", []).append(
            f"[{stage}] 环节失败：{archive['failure_reason']}"
        )
        save_run(archive)
        raise

    save_run(archive)
    return archive


# --------------------------------------------------------------------------
# 从存档复算
# --------------------------------------------------------------------------

def _samples_from_history(history: list) -> list:
    """把存档里的原始 6 点转回 B06.SPEC.Sample。"""
    from B06.SPEC import Sample

    samples = []
    for h in history or []:
        samples.append(Sample(
            timestamp=datetime.fromisoformat(h["timestamp"]),
            temperature=h["temperature"],
            humidity=h["humidity"],
            lux=h["lux"],
            source_type=h.get("source_type", ""),
            sim_scenario=h.get("sim_scenario", ""),
        ))
    return samples


def recompute_from_archive(archive: dict) -> dict:
    """只用存档里的数据，把事实性内容重新算一遍。

    这是"事后可回溯"的核心：不是复读当初的字符串，而是重新推导。
    """
    from B07.stability import judge_stability

    history = archive.get("history") or []
    samples = _samples_from_history(history)

    judged = judge_stability(samples) if samples else {
        "passed": False, "criteria": [],
        "conclusion": "存档内无历史点，无法复算稳定判定",
    }

    # 当前状态：优先用存档的 current_state，并与历史末点对照
    current = archive.get("current_state") or {}
    last_point = history[-1] if history else {}

    # hours_gap：重新算
    hours_gap = None
    lid = archive.get("latest_in_db") or {}
    if lid.get("status") == "ok" and archive.get("analysis_time"):
        db_last = datetime.fromisoformat(lid["timestamp"])
        anchor = datetime.fromisoformat(archive["analysis_time"])
        hours_gap = round((db_last - anchor).total_seconds() / 3600.0, 2)

    recomputed = {
        "current_state": current,
        "history_count": len(history),
        "history_start": history[0]["timestamp"] if history else None,
        "history_end": history[-1]["timestamp"] if history else None,
        "stability": {
            "passed": judged["passed"],
            "criteria": judged["criteria"],
            "conclusion": judged["conclusion"],
        },
        "hours_gap": hours_gap,
        "predicted_attention": (archive.get("model_output") or {}).get("predicted_attention"),
        "model_version": (archive.get("model_output") or {}).get("model_version"),
        "confidence": (archive.get("model_output") or {}).get("confidence"),
    }

    # ---- 一致性对照：存档值 vs 复算值 ----
    checks: list[dict] = []

    def _add(name, archived, new, note=None):
        checks.append({
            "name": name,
            "archived": archived,
            "recomputed": new,
            "match": (archived == new),
            "note": note,
        })

    hw = archive.get("history_window") or {}
    _add("历史点数", hw.get("count"), len(history))
    _add("窗口起点", hw.get("start"), recomputed["history_start"])
    _add("窗口终点", hw.get("end"), recomputed["history_end"])
    _add("稳定判定 passed", (hw.get("stability") or {}).get("passed"), judged["passed"])
    _add("稳定判定结论", (hw.get("stability") or {}).get("conclusion"), judged["conclusion"])
    _add("hours_gap", archive.get("hours_gap"), hours_gap)
    _add("当前状态 温度", current.get("temperature"), last_point.get("temperature"))
    _add("当前状态 湿度", current.get("humidity"), last_point.get("humidity"))
    _add("当前状态 光照", current.get("lux"), last_point.get("lux"))
    _add("当前状态 采样时间", current.get("timestamp"), last_point.get("timestamp"))
    _add("source_type", current.get("source_type"), last_point.get("source_type"))
    _add(
        "predicted_attention",
        (archive.get("model_output") or {}).get("predicted_attention"),
        recomputed["predicted_attention"],
        note="需重新加载 B06 模型才可独立复算，此处为存档值",
    )

    return {"recomputed": recomputed, "checks": checks}


# --------------------------------------------------------------------------
# 回溯
# --------------------------------------------------------------------------

def _fmt(v, digits=1):
    if v is None:
        return "未获取"
    if isinstance(v, float):
        return f"{v:.{digits}f}"
    return str(v)


def explain_run(run_id: str, quiet: bool = False) -> dict:
    """回溯一次运行：完全从存档文件读取，并重新算一遍事实性内容。

    Args:
        run_id: run 编号，如 run_001
        quiet: True 时不打印（调用方自己组织输出）

    Returns:
        {
          "run_id", "status", "created_at", "answer"（回答正文）,
          "recomputed", "checks", "archive"（原始存档）, "source_path"
        }
    """
    archive = load_run(run_id)
    path = run_json_path(run_id)

    # ---- 失败存档：直接展示失败信息 ----
    if archive.get("status") != "success":
        cs = archive.get("current_state") or {}
        mo = archive.get("model_output") or {}
        lines = [
            f"【{run_id} 回溯】该次运行失败，以下是留痕信息：",
            f"  创建时间:   {archive.get('created_at')}",
            f"  失败环节:   {archive.get('failed_stage')}",
            f"  失败原因:   {archive.get('failure_reason')}",
            f"  时间锚点:   {archive.get('analysis_time')}",
        ]
        if cs:
            lines.append(
                f"  已取得的证据：当前状态 {cs.get('temperature')}℃ / {cs.get('humidity')}% / "
                f"{cs.get('lux')} lux @ {cs.get('timestamp')}（source_type={cs.get('source_type')}）"
            )
        if mo:
            lines.append(
                f"  已取得的证据：模型预测 predicted_attention={mo.get('predicted_attention')}，"
                f"model_version={mo.get('model_version')}"
            )
        lines += [
            "",
            "（失败运行同样有存档：这次尝试发生过、卡在哪一步是可查的，不是静默吞掉）",
        ]
        answer = "\n".join(lines)
        if not quiet:
            print("=" * 70)
            print(answer)
            print("=" * 70)
        return {
            "run_id": run_id,
            "status": archive.get("status"),
            "created_at": archive.get("created_at"),
            "answer": answer,
            "recomputed": {},
            "checks": [],
            "archive": archive,
            "source_path": str(path),
        }

    rc = recompute_from_archive(archive)
    recomputed = rc["recomputed"]
    checks = rc["checks"]
    all_match = all(c["match"] for c in checks if c["name"] != "predicted_attention")

    cs = recomputed["current_state"]
    stab = recomputed["stability"]
    mo = archive.get("model_output") or {}
    sj = archive.get("structured_judgement") or {}
    llm_meta = archive.get("llm_meta") or {}
    src = archive.get("evidence_sources") or {}
    warnings = archive.get("warnings") or []

    verdict = sj.get("predicted_attention") or mo.get("predicted_attention")
    stability_verdict = "稳定" if stab["passed"] else "不稳定"

    # ---- 回答正文（事实全部取自复算结果）----
    crit = {c["name"]: c for c in stab["criteria"]}
    answer_parts = [
        f"【{run_id} 回溯】本次分析创建于 {archive.get('created_at')}，"
        f"锚点时刻 {archive.get('analysis_time')}，位置 {archive.get('location')}。",

        "◆ 看到了什么",
        f"  当前状态：{_fmt(cs.get('temperature'))}℃ / {_fmt(cs.get('humidity'))}% / "
        f"{_fmt(cs.get('lux'), 0)} lux",
        f"            source_type={cs.get('source_type')}，采样时间 {cs.get('timestamp')}",
        f"  历史窗口：{recomputed['history_start']} → {recomputed['history_end']}"
        f"（{recomputed['history_count']} 点）",
        f"  库内最新：{(archive.get('latest_in_db') or {}).get('timestamp')}"
        f"（hours_gap={recomputed['hours_gap']} 小时）",
        f"  证据来源：{'、'.join(f'{k}={v}' for k, v in src.items())}",

        "◆ 为什么说它比较稳定（从存档的原始 6 点重跑 judge_stability）",
    ]
    for c in stab["criteria"]:
        icon = "✅" if c["passed"] else "❌"
        answer_parts.append(
            f"  {icon} {c['name']}：实测={c['value']}，阈值={c['threshold']} — {c['note']}"
        )
    answer_parts.append(f"  复算结论：{stab['conclusion']}")

    answer_parts += [
        "◆ 模型预测",
        f"  predicted_attention={mo.get('predicted_attention')}，"
        f"confidence={_fmt(mo.get('confidence'), 4)}，"
        f"model_version={mo.get('model_version')}",
        f"  DTW：温度 {_fmt(mo.get('dtw_temperature'), 2)} / "
        f"湿度 {_fmt(mo.get('dtw_humidity'), 2)} / 光照 {_fmt(mo.get('dtw_lux'), 1)}",
        f"  系数影响 Top5（{mo.get('feature_contribution_method')}）：",
    ]
    for i, c in enumerate(mo.get("coef_top5") or [], 1):
        answer_parts.append(
            f"    {i}. {c['feature']:<26} {c['coefficient']:>+9.4f}  {c['direction']}"
        )

    answer_parts.append("◆ 结构化判断（五字段，原样展示存档内容）")
    for name in ["current_environment", "recent_trend", "predicted_attention",
                 "evidence_status", "limitations"]:
        value = sj.get(name)
        if name == "limitations":
            answer_parts.append(f"  {name}:")
            for k in (value or {}).get("all_keys", []):
                answer_parts.append(f"    - [{k}] {(value or {}).get(k, {}).get('text')}")
        elif name == "evidence_status":
            answer_parts.append(f"  {name}: {(value or {}).get('summary')}")
            for item in (value or {}).get("items", []):
                icon = "✅" if item["present"] else "⭕"
                answer_parts.append(f"    {icon} [{item['module']}] {item['item']}: {item['detail']}")
        elif name == "recent_trend":
            answer_parts.append(
                f"  {name}: {value.get('stability_verdict')}；{value.get('conclusion')}"
            )
        elif name == "current_environment":
            answer_parts.append(
                f"  {name}: 温度 {value.get('temperature')}℃，湿度 {value.get('humidity')}%，"
                f"光照 {value.get('lux')} lux，来源 {value.get('source_type')}，"
                f"采样于 {value.get('sampled_at')}"
            )
        else:
            answer_parts.append(f"  {name}: {value}")

    answer_parts.append("◆ LLM 解释（历史记录，原样展示，不参与上面的复算）")
    answer_parts.append(f"  {archive.get('llm_explanation')}")
    answer_parts.append(
        f"  — provider={llm_meta.get('provider')}，model={llm_meta.get('model')}，"
        f"elapsed_ms={llm_meta.get('elapsed_ms')}，content_length={llm_meta.get('content_length')}"
    )

    answer_parts.append("◆ warnings")
    if warnings:
        for w in warnings:
            answer_parts.append(f"  ⚠️  {w}")
    else:
        answer_parts.append("  无")

    answer_parts.append(
        "◆ 复算一致性（存档值 vs 本次从存档重算的值）: "
        + ("全部一致 ✅" if all_match else "存在差异 ⚠️")
    )
    for c in checks:
        flag = "✅" if c["match"] else "⚠️"
        note = f"（{c['note']}）" if c.get("note") else ""
        answer_parts.append(
            f"  {flag} {c['name']}: 存档={c['archived']} / 复算={c['recomputed']}{note}"
        )

    answer = "\n".join(answer_parts)

    if not quiet:
        print("=" * 70)
        print(answer)
        print("=" * 70)

    return {
        "run_id": run_id,
        "status": archive.get("status"),
        "created_at": archive.get("created_at"),
        "answer": answer,
        "recomputed": recomputed,
        "checks": checks,
        "all_match": all_match,
        "archive": archive,
        "source_path": str(path),
    }


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main():
    import argparse

    parser = argparse.ArgumentParser(description="B07 Step 1（第五步）— 运行存档与回溯")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="跑一次完整分析并存档")
    p_run.add_argument("--anchor", type=str, default="2026-10-21T16:00:00")
    p_run.add_argument("--location", type=str, default=DEFAULT_LOCATION)
    p_run.add_argument("--model", type=str, default=str(DEFAULT_MODEL_PATH))

    p_list = sub.add_parser("list", help="列出所有 run")
    p_list.add_argument("--json", action="store_true")

    p_exp = sub.add_parser("explain", help="回溯某次运行")
    p_exp.add_argument("run_id", type=str, help="如 run_001")
    p_exp.add_argument("--json", action="store_true")

    args = parser.parse_args()

    if args.cmd == "run":
        print("=" * 70)
        print("B07 Step 1（第五步）— 执行一次完整分析并存档")
        print("=" * 70)
        try:
            archive = run_analysis(args.anchor, args.location, args.model)
        except Exception as e:
            print(f"\n  ❌ 分析失败（已留痕，见 B07/runs/ 下的失败存档）：\n  {e}")
            sys.exit(1)
        print(f"\n  run_id:      {archive['run_id']}")
        print(f"  status:      {archive['status']}")
        print(f"  verdict:     {archive['structured_judgement']['predicted_attention']}")
        print(f"  存档文件:    {run_json_path(archive['run_id'])}")
        print(f"\n  回溯：python -m B07.runs explain {archive['run_id']}")

    elif args.cmd == "list":
        items = list_runs()
        if args.json:
            print(json.dumps(items, ensure_ascii=False, indent=2))
            return
        print("=" * 70)
        print("B07 运行存档索引")
        print("=" * 70)
        if not items:
            print("  （还没有任何 run）")
            return
        for it in items:
            icon = "✅" if it.get("status") == "success" else "❌"
            print(f"  {icon} {it['run_id']}  {it.get('created_at')}  "
                  f"verdict={it.get('verdict')}  model={it.get('model_version')}")
            print(f"      question: {it.get('question')}")
            if it.get("failed_stage"):
                print(f"      失败环节: {it.get('failed_stage')}")

    elif args.cmd == "explain":
        try:
            result = explain_run(args.run_id)
        except RunError as e:
            print(f"\n  ❌ {e}")
            sys.exit(1)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
