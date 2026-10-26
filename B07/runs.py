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

#: 允许出现在存档里的 **token 计数** 键名（**精确匹配**，不是子串放行）。
#:
#: prompt_tokens / completion_tokens / total_tokens / cached_tokens 是**计数**，
#: 不是密钥。S4-3 要把它们存进 llm_meta，用来做"两组共享前缀逐字节相同"
#: 的机器判定。
#:
#: 只精确放行这几个名字；"access_token"、"api_token"、"token" 这类
#: 仍然是子串命中，照样拦得住。
SAFE_TOKEN_COUNT_KEYS = frozenset({
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "cached_tokens",
    "prompt_tokens_details",
})


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
            if (kl not in SAFE_TOKEN_COUNT_KEYS
                    and any(h in kl for h in FORBIDDEN_KEY_HINTS)):
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
    index["runs"][run_id] = _index_entry(run_id, archive, path)
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


def _index_entry(run_id: str, archive: dict, path: Path) -> dict:
    """把一份存档压缩成索引里的一条记录。

    scenario 取存档里的真实值；旧存档没有这个字段 → None（显示 "(旧存档未记录)"）。
    """
    try:
        rel_path = str(path.relative_to(_PROJECT_ROOT)).replace("\\", "/")
    except ValueError:
        rel_path = str(path).replace("\\", "/")

    # S3-3：合成关注等级。存档里没有这个字段的老存档，
    # 按 judgement.py 的规则从存档复算——这条规则本来就该能脱离 LLM 复算。
    ca = archive.get("combined_attention") or {}
    if not ca:
        try:
            from B07.judgement import build_combined_attention
            ca = build_combined_attention(archive)
        except Exception:
            ca = {}

    return {
        "created_at": archive.get("created_at"),
        "scenario": archive.get("scenario"),
        "question": archive.get("question"),
        "verdict": (archive.get("structured_judgement") or {}).get("predicted_attention")
                   or (archive.get("model_output") or {}).get("predicted_attention"),
        "model_version": (archive.get("model_output") or {}).get("model_version"),
        "status": archive.get("status"),
        "failed_stage": archive.get("failed_stage"),
        # S3-3：合成关注等级（可脱离 LLM 复算）
        "combined_attention": ca.get("value"),
        # 存档里的备注（例如失败尝试的说明）
        "note": archive.get("note"),
        "path": rel_path,
    }


def rebuild_index() -> dict:
    """按 B07/runs/ 下**实际存在**的存档目录重建 index.json。

    做两件事：
      1. 丢弃指向已删除 run 的索引条目（例如手工删掉调试 run 之后）
      2. 重算 next_seq = 现有最大编号 + 1（不复用已删编号）

    每条记录都带 scenario 字段；旧存档没有该字段时为 null（正常情况）。
    """
    runs: dict = {}
    max_seq = 0

    if RUNS_DIR.exists():
        for d in sorted(RUNS_DIR.iterdir()):
            if not d.is_dir() or not d.name.startswith(RUN_ID_PREFIX):
                continue
            p = d / "run.json"
            if not p.exists():
                print(f"  ⚠️  {d.name}/ 里没有 run.json，跳过", file=sys.stderr)
                continue
            try:
                with open(p, encoding="utf-8") as f:
                    archive = json.load(f)
            except (json.JSONDecodeError, OSError) as e:
                print(f"  ⚠️  {d.name}/run.json 读不出来，跳过："
                      f"{type(e).__name__}: {e}", file=sys.stderr)
                continue

            run_id = archive.get("run_id") or d.name
            runs[run_id] = _index_entry(run_id, archive, p)
            digits = run_id[len(RUN_ID_PREFIX):]
            if digits.isdigit():
                max_seq = max(max_seq, int(digits))

    index = {
        "version": INDEX_VERSION,
        "next_seq": max_seq + 1,
        "runs": runs,
    }
    _save_index(index)
    return index


# --------------------------------------------------------------------------
# 跑一次完整分析并存档
# --------------------------------------------------------------------------

def _provider_name() -> str:
    """从 LLM_BASE_URL 取主机名当 provider。只存主机名，不存 key。"""
    base = os.environ.get("LLM_BASE_URL", "")
    host = base.split("//")[-1].split("/")[0]
    return host or "unknown"


def scenario_label(archive: dict) -> str:
    """存档的场景标签。

    旧存档（run_001 / run_008 / run_009）没有 scenario 字段，属于正常情况，
    取不到就显示 "(旧存档未记录)"，不报错。
    """
    return archive.get("scenario") or "(旧存档未记录)"


def run_analysis(anchor: datetime | str, location: str = DEFAULT_LOCATION,
                 model_path: str | Path = DEFAULT_MODEL_PATH,
                 window_selection: dict | None = None,
                 progress=None, scenario: str = "S1",
                 external_conditions: dict | None = None,
                 evidence_ledger=None,
                 llm_context_override: str | None = None) -> dict:
    """执行一次完整分析（S1-2 → S1-3 → S1-4），把产物拼成快照落盘。

    Args:
        anchor:           时间锚点
        location:         位置标识
        model_path:       B06 已存模型产物路径
        window_selection: 选窗结果，原样存进存档（可选）
        progress:         阶段回调 progress(stage, extra)，供上层打印进度（可选）
        scenario:         场景 key（S1 / S2）。同一套流程，只是看哪一段历史。
        external_conditions: 可选的外部天气条件（S3-1）。
            传 None（默认）时行为与原来完全一致：结构化判断仍是 5 个字段。
        evidence_ledger:  可选的证据台账（S4-2）。可以是
            B07.evidence_ledger.EvidenceLedger 对象、它的可序列化 dict，
            或一个接受 run_bundle 的可调用对象（现场装配台账）。
            不传（默认）时行为与现在**完全一致**：结构化判断仍是 5/6 个字段，
            存档里也没有 evidence_ledger 这一项。
        llm_context_override: **可选** —— 直接指定发给 LLM 的上下文原文。
            不传（默认）时用 format_judgement_context(bundle)，行为与现在完全一致。
            S4-3 的 A/B 对照实验用它注入两个"只有证据组织方式不同"的上下文。
            注意：它只改**发给 LLM 的那串文字**，判定、存档、复算逻辑一律不动。

    Returns: 存档 dict（已落盘）

    任一环节失败：
      - 先落一份 status="failed" 的 run.json（含 failed_stage / failure_reason）
      - 再把异常抛出（调用方应感知失败）
    """
    from B07.evidence import build_evidence
    from B07.predict import predict_for_run
    from B07.judgement import (build_combined_attention, build_judgement,
                               self_check)
    from B07.explain import (
        FIXED_QUESTION, explain_judgement, compare_judgement_vs_explanation,
    )
    from B07.stability import judge_stability
    from B07.scenarios import get_scenario, rich_failure_for

    if isinstance(anchor, str):
        anchor = datetime.fromisoformat(anchor)

    # 场景只允许改"看哪一段历史"，问题必须与 S1 一字不差
    scenario_def = get_scenario(scenario)
    if scenario_def["question"] != FIXED_QUESTION:
        raise RunError(f"场景 {scenario} 的问题与固定问题不一致，已中止")

    rich = rich_failure_for(scenario)

    run_id = _reserve_run_id()
    created_at = datetime.now().isoformat(timespec="seconds")

    archive: dict = {
        "run_id": run_id,
        "created_at": created_at,
        "status": "running",
        "failed_stage": None,
        "failure_reason": None,
        "scenario": scenario,
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
        # S3-3：合成关注等级（内部 B06 预测 × 外部天气信号，纯程序规则）
        "combined_attention": None,
        "llm_explanation": None,
        "llm_meta": {},
        # S3-2：存下发给 LLM 的原文上下文（用于两次运行的逐字节对比）
        "llm_context": None,
        "evidence_sources": {},
        "warnings": [],
        "window_selection": window_selection,
        # S3-1：外部天气条件（默认 None = 本次不接外部条件）
        "external_conditions": external_conditions,
        # S4-2：证据台账（原样存进存档；默认 None = 本次不接台账）
        "evidence_ledger": None,
    }

    def _notify(stage_name: str, extra=None):
        if progress is not None:
            try:
                progress(stage_name, extra)
            except Exception as e:
                # 进度回调出问题不能影响主流程，但必须留痕。
                # 曾经这里是一句 `except: pass`，把一个 NameError 静默吞掉，
                # 表现为某一阶段什么都不打印——查起来非常费时间。
                print(f"[B07] 进度回调异常（stage={stage_name}）："
                      f"{type(e).__name__}: {e}", file=sys.stderr)

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
        judged = judge_stability(samples, rich_failure=rich)
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
        # 场景随 bundle 传下去，判断环节据此决定用哪种失败结论句
        evidence["scenario"] = scenario
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
        # 有外部条件时，它是结构化判断的第 6 个字段（追加，不是替换）
        if external_conditions is not None:
            bundle["external_conditions"] = external_conditions

        # S4-2：证据台账。台账可能是一个"接受 run_bundle 的可调用对象"
        #（现场装配：历史 / 模型 / 天气 / 知识各自取数后再登记），
        # 也可以直接给台账对象或它的可序列化 dict。
        ledger = evidence_ledger
        if callable(ledger):
            ledger = ledger(bundle)
        if ledger is not None:
            ledger_data = (ledger.to_dict() if hasattr(ledger, "to_dict")
                           else dict(ledger))
            archive["evidence_ledger"] = ledger_data
            # S5-1：把台账挂进 bundle，让 ⑦ 块能列出"当前不可用的证据"
            # （谁不可用、status 是什么、失败原因是什么）。
            bundle["evidence_ledger"] = ledger

        judgement = build_judgement(bundle, evidence_ledger=ledger)
        bundle["judgement"] = judgement
        archive["structured_judgement"] = judgement
        archive["judgement_self_check"] = self_check(bundle,
                                                     evidence_ledger=ledger)
        # S3-3：合成关注等级。写死在程序里的规则（judgement.combined_attention），
        # 不经过 LLM，回溯时用同一份存档即可复算。
        archive["combined_attention"] = build_combined_attention(archive)

        _notify("judgement", {
            "judgement": judgement,
            "self_check": archive["judgement_self_check"],
        })

        # ---------------- S1-4B LLM 解释 ----------------
        stage = "llm_explanation"
        t0 = time.time()
        explanation = explain_judgement(bundle, judgement,
                                        context=llm_context_override)
        elapsed_ms = int((time.time() - t0) * 1000)

        archive["llm_explanation"] = explanation["answer"]
        # S3-2：把发给 LLM 的上下文原文也存下来（对比两次运行时用来证明
        # "只有 ⑥ 块不同，其余逐字节相同"）
        archive["llm_context"] = explanation.get("context")
        archive["llm_meta"] = {
            "provider": _provider_name(),
            "model": explanation["model"],
            "elapsed_ms": elapsed_ms,
            "content_length": explanation["content_length"],
            "question": explanation["question"],
            # S4-3：token 计数（prompt_tokens / cached_tokens 用来机器判定
            # "两组共享前缀逐字节相同"）
            "usage": explanation.get("usage") or {},
            # 提示词指纹：S1 / S2 存档可直接比对"同一套提示词、同一个问题"
            "system_prompt_sha256": explanation.get("system_prompt_sha256"),
            "question_sha256": explanation.get("question_sha256"),
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
        # 失败也要在 stderr 留一行：谁在跑、卡在哪、什么原因
        print(f"[B07] 运行失败（run_id={run_id}，stage={stage}）："
              f"{type(e).__name__}: {e}", file=sys.stderr)
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


def recompute_external_conditions(archive: dict) -> dict:
    """从**存档文件**里的外部条件点位重新算一遍外部判定。

    刻意不依赖任何内存状态，也不重新联网取天气：
    只用存档里已经落盘的两个点 + 判定规则重算，
    这样"存档的结论"和"现在按同样规则算出来的结论"必须一致。

    判据来源：B07.weather.judge_external_points（与在线取数路径**同一份**）。
    """
    ext = archive.get("external_conditions")
    if not isinstance(ext, dict) or not ext:
        return {
            "present": False,
            "recomputed": None,
            "checks": [],
            "note": "本次运行没有外部条件（external_conditions 为空），无需复算",
        }

    from B07.weather import _WINDOW_TO_HOURS, judge_external_points

    window = ext.get("forecast_window")
    hours = _WINDOW_TO_HOURS.get(window, 0)

    baseline_point = ext.get("baseline_point")
    forecast_point = ext.get("forecast_point")

    anchor = None
    if archive.get("analysis_time"):
        anchor = datetime.fromisoformat(archive["analysis_time"])

    judged = judge_external_points(baseline_point, forecast_point, hours=hours,
                                    analysis_time=anchor)

    # future_ok 也重算一遍：预报时刻必须晚于存档里的 analysis_time
    future_ok = None
    if forecast_point and anchor is not None:
        from B07.weather import _parse_forecast_time, _to_comparable

        fore_dt = _parse_forecast_time(forecast_point.get("timestamp"))
        if fore_dt is not None:
            future_ok = _to_comparable(fore_dt) > _to_comparable(anchor)

    recomputed = {
        "signal": judged["signal"],
        "judge_method": judged["judge_method"],
        "basis": judged["basis"],
        "precip_delta": judged["precip_delta"],
        "temp_delta": judged["temp_delta"],
        "cloud_delta": judged["cloud_delta"],
        "solar_radiation": judged["solar_radiation"],
        "future_ok": future_ok,
    }

    checks: list[dict] = []

    def _add(name, archived, new, note=None):
        checks.append({
            "name": name,
            "archived": archived,
            "recomputed": new,
            "match": (archived == new),
            "note": note,
        })

    _add("外部判定 signal", ext.get("signal"), recomputed["signal"])
    _add("外部判定 judge_method", ext.get("judge_method"), recomputed["judge_method"])
    _add("外部降水概率 Δpp", ext.get("precip_delta"), recomputed["precip_delta"])
    _add("外部温度 Δ℃", ext.get("temp_delta"), recomputed["temp_delta"])
    _add("外部云量 Δ%", ext.get("cloud_delta"), recomputed["cloud_delta"])
    if future_ok is not None:
        _add("外部 future_ok", ext.get("future_ok"), recomputed["future_ok"])

    return {
        "present": True,
        "recomputed": recomputed,
        "checks": checks,
        "note": "外部判定已按存档中的两个点重算",
    }


def recompute_from_archive(archive: dict) -> dict:
    """只用存档里的数据，把事实性内容重新算一遍。

    这是"事后可回溯"的核心：不是复读当初的字符串，而是重新推导。
    """
    from B07.stability import judge_stability
    from B07.scenarios import rich_failure_for

    history = archive.get("history") or []
    samples = _samples_from_history(history)

    # 场景决定失败结论句的写法；老存档没有 scenario 字段，按 S1 处理
    rich = rich_failure_for(archive.get("scenario"))

    judged = judge_stability(samples, rich_failure=rich) if samples else {
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

    # ---- S3-2：外部条件也按存档里的点重算一遍（不依赖内存状态）----
    ext_rc = recompute_external_conditions(archive)
    recomputed["external_conditions"] = ext_rc["recomputed"]
    checks.extend(ext_rc["checks"])

    # ---- S3-3：合成关注等级用同一份存档复算（纯规则，不依赖 LLM）----
    from B07.judgement import build_combined_attention
    ca = build_combined_attention(archive)
    recomputed["combined_attention"] = ca
    archived_ca = archive.get("combined_attention") or {}
    _add("合成关注等级 combined_attention",
         archived_ca.get("value"),
         ca["value"],
         note=ca["reason"])

    # ---- S4-2：证据分歧按存档里的台账重算（方向是规则算的，不是复读）----
    ledger_data = archive.get("evidence_ledger")
    archived_div = (archive.get("structured_judgement") or {}).get(
        "evidence_divergence")
    if ledger_data or archived_div is not None:
        from B07.evidence_ledger import EvidenceLedger
        from B07.judgement import build_evidence_divergence

        if ledger_data:
            rebuilt = EvidenceLedger.from_dict(ledger_data)
            new_div = build_evidence_divergence(rebuilt)
        else:
            new_div = None
        recomputed["evidence_divergence"] = new_div
        _add("证据分歧复算", archived_div, new_div,
             note="按存档里的证据台账重算方向与三列表（方向是程序规则算的）")

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
    # 旧存档（run_001 / run_008 / run_009）没有 scenario 字段，属于正常
    scene_text = scenario_label(archive)

    # ---- 回答正文（事实全部取自复算结果）----
    crit = {c["name"]: c for c in stab["criteria"]}
    answer_parts = [
        f"【{run_id} 回溯】本次分析创建于 {archive.get('created_at')}，"
        f"锚点时刻 {archive.get('analysis_time')}，位置 {archive.get('location')}。",
        f"  场景：{scene_text}",

        "◆ 看到了什么",
        f"  当前状态：{_fmt(cs.get('temperature'))}℃ / {_fmt(cs.get('humidity'))}% / "
        f"{_fmt(cs.get('lux'), 0)} lux",
        f"            source_type={cs.get('source_type')}，采样时间 {cs.get('timestamp')}",
        f"  历史窗口：{recomputed['history_start']} → {recomputed['history_end']}"
        f"（{recomputed['history_count']} 点）",
        f"  库内最新：{(archive.get('latest_in_db') or {}).get('timestamp')}"
        f"（hours_gap={recomputed['hours_gap']} 小时）",
        f"  证据来源：{'、'.join(f'{k}={v}' for k, v in src.items())}",

        ("◆ 为什么说它比较稳定（从存档的原始 6 点重跑 judge_stability）"
         if stab.get("passed")
         else "◆ 为什么说它不稳定（从存档的原始 6 点重跑 judge_stability）"),
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
    field_names = ["current_environment", "recent_trend", "predicted_attention",
                   "evidence_status", "limitations"]
    # S3-1：接了外部条件时多一个字段
    if sj.get("external_conditions"):
        field_names.append("external_conditions")
    for name in field_names:
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
        elif name == "external_conditions":
            answer_parts.append(
                f"  {name}: {value.get('signal')}（{value.get('judge_method')}）"
            )
            answer_parts.append(f"    依据：{value.get('basis')}")
            answer_parts.append(
                f"    预报点：{(value.get('forecast_point') or {}).get('timestamp')}"
            )
        else:
            answer_parts.append(f"  {name}: {value}")

    # S3-2：外部条件单独再展示一段——它是**从存档文件重读后复算**的结果，
    # 不是复读当初内存里那份对象。
    ext_rc = recompute_external_conditions(archive)
    if ext_rc.get("present"):
        ext_re = ext_rc["recomputed"] or {}
        answer_parts.append("◆ 外部条件复算（从存档文件里的两个点重算，不依赖内存状态）")
        answer_parts.append(f"  signal = {ext_re.get('signal')}"
                            f"（{ext_re.get('judge_method')}）")
        answer_parts.append(f"  降水概率 Δ {ext_re.get('precip_delta')} pp，"
                            f"温度 Δ {ext_re.get('temp_delta')} ℃，"
                            f"云量 Δ {ext_re.get('cloud_delta')} %，"
                            f"太阳辐射 {ext_re.get('solar_radiation')} W/m²")
        answer_parts.append(f"  future_ok = {ext_re.get('future_ok')}")
        answer_parts.append(f"  依据：{ext_re.get('basis')}")

    # S3-3：合成关注等级（纯程序规则，可脱离 LLM 复算）
    ca = (recomputed or {}).get("combined_attention") or {}
    if ca:
        answer_parts.append("◆ 合成关注等级（程序规则，不经过 LLM）")
        answer_parts.append(f"  combined_attention = {ca.get('value')}")
        answer_parts.append(f"  内部 predicted_attention = {ca.get('internal_predicted_attention')}"
                            f"，外部 signal = {ca.get('external_signal')}")
        answer_parts.append(f"  理由：{ca.get('reason')}")

    # S4-2：证据台账 + 证据分歧（同一份表，报告与回溯共用）
    ledger_data = archive.get("evidence_ledger")
    div = sj.get("evidence_divergence")
    if ledger_data or div:
        from B07.evidence_ledger import format_evidence_table

        answer_parts.append("◆ 证据台账（S4-2）")
        for row in format_evidence_table(ledger_data or {}):
            answer_parts.append(f"  {row}")
        adoption = (ledger_data or {}).get("adoption") or {}
        if adoption.get("text"):
            answer_parts.append(f"  {adoption['text']}")
        for na in adoption.get("not_adopted_calls") or []:
            answer_parts.append(
                f"  未采用：{na.get('tool_name')}（status={na.get('status')}）"
                f" —— {na.get('reason')}"
            )

    if div:
        answer_parts.append("◆ 证据分歧（程序规则，不经过 LLM）")
        answer_parts.append(f"  has_conflict = {div.get('has_conflict')}")
        answer_parts.append(f"  各来源方向："
                            + "、".join(f"{k}={v}"
                                        for k, v in (div.get("each_source") or {}).items()))
        answer_parts.append(f"  支持提高关注："
                            + ("、".join(f"[{i.get('source_type')}] {i.get('summary')}"
                                         for i in (div.get("supporting") or []))
                               or "（无）"))
        answer_parts.append(f"  提示可能缓解或反向："
                            + ("、".join(f"[{i.get('source_type')}] {i.get('summary')}"
                                         for i in (div.get("opposing") or []))
                               or "（无）"))
        answer_parts.append(f"  仍不足以确定："
                            + ("、".join(f"[{i.get('source_type')}] {i.get('summary')}"
                                         for i in (div.get("undetermined") or []))
                               or "（无）"))
        answer_parts.append(f"  分歧处理：{div.get('resolution')}")

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

    p_reb = sub.add_parser("rebuild-index",
                           help="按实际存在的存档重建 index.json（丢弃失效条目、重算 next_seq）")

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
            print(f"      scenario: {it.get('scenario') or '(旧存档未记录)'}")
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

    elif args.cmd == "rebuild-index":
        index = rebuild_index()
        print(f"  已重建 index.json：{len(index['runs'])} 条记录，"
              f"next_seq={index['next_seq']}")
        for run_id, item in index["runs"].items():
            print(f"    {run_id}  scenario={item.get('scenario') or '(旧存档未记录)'}"
                  f"  status={item.get('status')}")


if __name__ == "__main__":
    main()
