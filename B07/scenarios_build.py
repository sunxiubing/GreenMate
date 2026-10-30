"""B08 第一遍 · 固定场景的抽取与校验（文档 p.90–91）

★ 这一遍只做"冻结"，**不重跑任何分析、不调 LLM、不调天气 API**。

  场景文件 = 从**已有存档**里抽取出来的一份"固定输入"。
  抽取的是"当时那一次真的用过的那些输入"，不是重新生成的：

    current_state / history / history_window
        ← run.json 的原始字段，一字不改
    weather_evidence_snapshot
        ← 存档里**已经真实取得并保存**的天气证据
          · external_conditions：run.json 顶层那份外部条件上下文，**逐字**
          · ledger_b03_ext：证据台账里那条 B03 证据的 ext，**逐字**
            （provider / retrieved_at / forecast_time / status / error 都在这）
          · provider / retrieved_at / forecast_time：证据身份，
            每个字段都带来源标注（是从存档读到的，还是按既有约定补的）
    knowledge_evidence_snapshot
        ← 存档里那条 B04 证据（citations + has_relevant）

★ 文档 p.90–91 要求区分两类验证，这里体现在**来源标注**上：

    (1) 真实链路验证：重新调 B03 真实天气 API（**不在本模块做**）
    (2) 固定场景回放 / 回归：用**快照**，条件不漂移  ← 本模块与后续回归用这个

    所以每个快照都带 `is_constructed` 与 `snapshot_taken_from_run_id`，
    一眼能看出"这是快照，不是实时天气"。

用法：
    python -m B07.scenarios_build              # 生成/刷新五个场景文件
    python -m B07.scenarios_build --verify     # 只校验，不写盘
    python -m B07.scenarios_build --refresh-source   # 重新挑来源 run
    python -m B07.scenarios_build --source S1=run_113   # 手动指定来源

★ 硬约束：
  · 不改 run.json 的存档结构（一个字节都不动）；
  · 不重跑分析、不调 LLM、不调天气 API；
  · 每个场景文件末尾的 scenario_sha256 让"场景有没有被手改过"可机器判定。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from B07.explain import FIXED_QUESTION, SYSTEM_PROMPT, _sha12

#: 固定场景目录
SCENARIOS_DIR = _PROJECT_ROOT / "B07" / "scenarios"

#: run 存档目录
RUNS_DIR = _PROJECT_ROOT / "B07" / "runs"

#: B08 基线版本号（与 B07/health.py 一致）
APP_VERSION = "B08_baseline_01"

#: 固定问题（**只读引用**，绝不修改）
QUESTION = FIXED_QUESTION
QUESTION_SHA256 = _sha12(QUESTION)
SYSTEM_PROMPT_SHA256 = _sha12(SYSTEM_PROMPT)

#: 五个场景的定义：名称 + 在存档里对应的 scenario 取值
#:
#: `run_scenario` —— 回放时传给 B07.runs.run_analysis 的 scenario 键
#: （必须与来源存档的 scenario 一字不差，否则 run_analysis 会拒绝）。
#: `evidence_ledger` —— 该场景回放时**是否装配证据台账**：
#:   S1/S2/S3 的来源存档本来就没有台账（照抄，不新造）；
#:   S4/S5 有台账（S4-2/S5-1 的判定形态就是靠台账表达），回放时按快照重建。
SCENARIO_SPECS = {
    "S1": {"name": "稳态基线", "archive_scenario": "S1",
           "run_scenario": "S1", "evidence_ledger": False},
    "S2": {"name": "渐进漂移", "archive_scenario": "S2",
           "run_scenario": "S2", "evidence_ledger": False},
    "S3": {"name": "外部扰动", "archive_scenario": "S3_with",
           "run_scenario": "S3_with", "evidence_ledger": False},
    "S4": {"name": "多源证据分歧", "archive_scenario": "S4",
           "run_scenario": "S4", "evidence_ledger": True},
    "S5": {"name": "证据退化", "archive_scenario": "S5",
           "run_scenario": "S5", "evidence_ledger": True},
}

#: B03 的 provider 名（**代码常量**，不是从存档里读到的）
#:
#: ★ 为什么允许引用它：S1/S2/S3 当时的存档没有证据台账，
#:   run.json.external_conditions 也没记 provider —— 但那次取数确实走的
#:   B03 的 Open-Meteo 通道。快照里用 `provider_recorded_in_archive=false`
#:   明确标注"这不是从存档读到的"，不许假装它是。
B03_PROVIDER_CONST = "Open-Meteo"
B03_PROVIDER_SOURCE = "B03.weather.OPEN_METEO_BASE（代码常量）"


# --------------------------------------------------------------------------
# 小工具
# --------------------------------------------------------------------------

def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def canonical_json(obj) -> str:
    """规范化 JSON：键排序、无多余空白、保留中文。

    ★ 这是 scenario_sha256 的计算口径（写进了场景文件本身，可复核）。
    """
    return json.dumps(obj, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))


def scenario_digest(obj: dict) -> str:
    """场景指纹：去掉 scenario_sha256 字段后的规范化 JSON 的 sha256。"""
    body = {k: v for k, v in obj.items() if k != "scenario_sha256"}
    return _sha256_bytes(canonical_json(body).encode("utf-8"))


def run_path(run_id: str) -> Path:
    return RUNS_DIR / run_id / "run.json"


def load_archive(run_id: str) -> dict:
    p = run_path(run_id)
    if not p.exists():
        raise FileNotFoundError(f"找不到存档：{p}")
    return json.loads(p.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# 来源 run 的挑选
# --------------------------------------------------------------------------

def _iter_runs() -> list[dict]:
    out = []
    for d in sorted(RUNS_DIR.glob("run_*")):
        p = d / "run.json"
        if not p.exists():
            continue
        try:
            j = json.loads(p.read_text(encoding="utf-8"))
        except Exception:                   # noqa: BLE001
            continue
        j["_path"] = str(p)
        out.append(j)
    return out


def _is_degraded(archive: dict) -> bool:
    """这次运行里天气证据是不是"不可用"（S5 的退化态）。"""
    led = archive.get("evidence_ledger") or {}
    for e in led.get("entries", []):
        if e.get("source_type") == "B03":
            return e.get("status") != "ok"
    ec = archive.get("external_conditions") or {}
    if ec:
        return not ec.get("available", True)
    return False


def pick_source(scenario_id: str, runs: list[dict] | None = None) -> str | None:
    """挑选该场景的来源 run。

    规则（写进了场景文件与冻结报告，可复核）：
      · 必须是 status == "success" 的存档；
      · 场景取值必须匹配（S3 → S3_with）；
      · S5 必须挑**天气证据不可用**的那一条（退化场景本身就是要被固定的）；
      · 同一条件下取 created_at **最新**的一条。
    """
    spec = SCENARIO_SPECS[scenario_id]
    runs = runs if runs is not None else _iter_runs()
    cand = [r for r in runs
            if r.get("status") == "success"
            and r.get("scenario") == spec["archive_scenario"]]
    if scenario_id == "S5":
        degraded = [r for r in cand if _is_degraded(r)]
        if degraded:
            cand = degraded
    if not cand:
        return None
    cand.sort(key=lambda r: (str(r.get("created_at") or ""), str(r.get("run_id"))))
    return cand[-1].get("run_id")


# --------------------------------------------------------------------------
# 快照抽取
# --------------------------------------------------------------------------

def _weather_snapshot(archive: dict) -> tuple[dict | None, list[str]]:
    """抽取天气证据快照。返回 (快照 or None, 说明列表)。

    ★ 快照的结构（B08 第二遍起固定）：

        external_conditions   ← 存档里那份**逐字**的外部条件上下文
                               （回放时原样注入，不重新构造、不重新取数）
        ledger_b03_ext       ← 证据台账里那条 B03 证据的 ext（**逐字**）
                               （provider / retrieved_at / forecast_time /
                                 status / error 都在这里）
        provider / retrieved_at / forecast_time
                             ← 证据身份。**每个字段都带来源标注**，
                               标明它是"从存档读到的"还是"按既有约定补的"。

    身份字段放在哪儿是**有讲究**的：
      · S4/S5 有台账 → 身份在台账的 EV-B03.ext 里（唯一出处）；
      · S3 没台账   → 身份只能在存档的 external_conditions 里，
                      否则回放出来的 run 会把身份整个丢掉。
    """
    notes: list[str] = []
    run_id = archive.get("run_id")

    # ---- 优先：证据台账里那条 B03 证据（provider/retrieved_at 都在 ext 里）----
    led = archive.get("evidence_ledger") or {}
    for e in led.get("entries", []):
        if e.get("source_type") != "B03":
            continue
        ext = e.get("ext") or {}
        res = e.get("result") or {}
        fc = res.get("forecast_point") or {}
        bp = res.get("baseline_point") or {}
        snap = {
            # —— 证据身份（**逐字**来自台账的 ext）——
            "provider": ext.get("provider"),
            "provider_recorded_in_archive": bool(ext.get("provider")),
            "retrieved_at": ext.get("retrieved_at"),
            "retrieved_at_recorded_in_archive": "retrieved_at" in ext,
            "forecast_time": ext.get("forecast_time") or fc.get("timestamp"),
            "forecast_time_recorded_in_archive": bool(ext.get("forecast_time")),
            # —— 外部条件上下文（**逐字**来自存档顶层 external_conditions）——
            "external_conditions": archive.get("external_conditions"),
            "ledger_b03_ext": ext,
            "case": ext.get("case"),
            "degraded": bool(ext.get("degraded")),
            "label": ext.get("label"),
            "analysis_time": (archive.get("external_conditions") or {}).get(
                "analysis_time") or archive.get("analysis_time"),
            "available": bool(e.get("status") == "ok"),
            "status": e.get("status"),
            "error": ext.get("error") or e.get("error"),
            "signal": res.get("signal"),
            "judge_method": res.get("judge_method"),
            "basis": res.get("basis"),
            "baseline_point": bp,
            "forecast_point": fc,
            "observed_at": bp.get("timestamp"),
            "precip_delta": res.get("precip_delta"),
            "temp_delta": res.get("temp_delta"),
            "future_ok": res.get("future_ok"),
            "warnings": res.get("warnings") or [],
            "caveats": res.get("caveats") or [],
            "is_constructed": ext.get("is_constructed"),
            "location": res.get("location"),
            "forecast_window": res.get("forecast_window"),
            "snapshot_taken_from_run_id": run_id,
            "snapshot_source": "evidence_ledger.entries[EV-B03] + "
                               "run.json.external_conditions",
        }
        if snap["is_constructed"]:
            notes.append(
                "天气证据来自**离线构造夹具**（is_constructed=true），"
                "不是真实天气；真实链路验证要单独做。")
        else:
            notes.append("天气证据来自证据台账，provider/retrieved_at/forecast_time "
                         "原样保留。")
        return snap, notes

    # ---- 次选：run.json 的 external_conditions（S1/S2/S3 没有台账）----
    ec = archive.get("external_conditions") or {}
    if ec:
        fc = ec.get("forecast_point") or {}
        bp = ec.get("baseline_point") or {}
        # is_constructed：run.json 没有这个字段，但存档自己的 warnings 写了
        # "外部来源为 PUBLIC 真实数据" —— 那就是**真实 API 数据**，不是构造的。
        # 依据存档自身的标注判定，不靠猜。
        warn_blob = " ".join(str(w) for w in (ec.get("warnings") or []))
        real_api = ("PUBLIC" in warn_blob and "真实数据" in warn_blob)
        if real_api:
            constructed = False
            constructed_note = ("is_constructed=false 依据存档 warnings 里的"
                                "『外部来源为 PUBLIC 真实数据』标注判定"
                                "（run.json 未显式记录该字段）。")
        else:
            constructed = None
            constructed_note = ("run.json 既没有 is_constructed 字段，warnings 里也"
                                "没有『PUBLIC 真实数据』标注，**无法判定**，留空。")

        # ★ retrieved_at：这份存档**没有**记录取数时刻。
        #   沿用项目里既有的约定（B07/evidence_registry.py:_archived_b03_weather
        #   对"存档里没有天气台账"的老存档也是用 created_at 当 retrieved_at），
        #   并在快照里写明来源，不假装它是存档原样记录的。
        retrieved_at = archive.get("created_at")
        snap = {
            "provider": B03_PROVIDER_CONST,
            "provider_recorded_in_archive": False,
            "provider_source": B03_PROVIDER_SOURCE,
            "retrieved_at": retrieved_at,
            "retrieved_at_recorded_in_archive": False,
            "retrieved_at_source": (
                f"{run_id}.created_at —— 该存档没有天气台账，"
                "run.json.external_conditions 也没记取数时刻；"
                "沿用 B07/evidence_registry.py:_archived_b03_weather() 的既有约定"
                "（用存档 created_at 作为 retrieved_at）"),
            "forecast_time": fc.get("timestamp"),
            "forecast_time_recorded_in_archive": bool(fc.get("timestamp")),
            "forecast_time_source": (
                "run.json.external_conditions.forecast_point.timestamp"
                "（B03 的 forecast_time 就是预报点时刻，见 B07/weather.py）"),
            # —— 外部条件上下文（**逐字**来自存档）——
            "external_conditions": ec,
            "ledger_b03_ext": None,
            "case": None,
            "degraded": not bool(ec.get("available")),
            "label": None,
            "analysis_time": ec.get("analysis_time") or archive.get("analysis_time"),
            "available": bool(ec.get("available")),
            "status": "ok" if ec.get("available") else "unavailable",
            "error": ec.get("error"),
            "signal": ec.get("signal"),
            "judge_method": ec.get("judge_method"),
            "basis": ec.get("basis"),
            "baseline_point": bp,
            "forecast_point": fc,
            "observed_at": bp.get("timestamp"),
            "precip_delta": ec.get("precip_delta"),
            "temp_delta": ec.get("temp_delta"),
            "future_ok": ec.get("future_ok"),
            "warnings": ec.get("warnings") or [],
            "caveats": ec.get("caveats") or [],
            "is_constructed": constructed,
            "location": ec.get("location"),
            "forecast_window": ec.get("forecast_window"),
            "snapshot_taken_from_run_id": run_id,
            "snapshot_source": "run.json.external_conditions",
        }
        notes.append(constructed_note)
        notes.append(
            "该存档的 run.json.external_conditions **不记录 provider / retrieved_at**"
            "（S1/S2/S3 当时没有证据台账）：provider 取自 B03 代码常量并标注 "
            "provider_recorded_in_archive=false；retrieved_at 按项目既有约定取 "
            "created_at 并标注 retrieved_at_recorded_in_archive=false —— "
            "两处都写明来源，不假装是存档原样记录的。")
        return snap, notes

    notes.append("该场景**不接入外部天气**（按设计），因此没有天气快照。")
    return None, notes


def _knowledge_snapshot(archive: dict) -> tuple[dict | None, list[str]]:
    """抽取知识证据快照（只有跑过 S4/S5 的存档才有台账里的 B04 证据）。"""
    led = archive.get("evidence_ledger") or {}
    for e in led.get("entries", []):
        if e.get("source_type") != "B04":
            continue
        res = e.get("result") or {}
        cites = res.get("citations") or []
        return {
            "query": res.get("query"),
            "top_k": res.get("top_k"),
            "has_relevant": res.get("has_relevant"),
            "note": res.get("note"),
            "citations": [
                {"chunk_id": c.get("chunk_id"),
                 "source_id": c.get("source_id"),
                 "section": c.get("section"),
                 "page": c.get("page"),
                 "score": c.get("score"),
                 "content": c.get("content")}
                for c in cites
            ],
            "status": e.get("status"),
            "retrieved_at": e.get("time"),
            "snapshot_taken_from_run_id": archive.get("run_id"),
            "snapshot_source": "evidence_ledger.entries[EV-B04]",
        }, []
    return None, ["该存档没有知识证据台账（该场景按设计不检索知识库）。"]


# --------------------------------------------------------------------------
# 场景构建
# --------------------------------------------------------------------------

def build_scenario(scenario_id: str, run_id: str) -> dict:
    """从一条存档构造场景对象（不含 scenario_sha256 之外的写入动作）。"""
    spec = SCENARIO_SPECS[scenario_id]
    archive = load_archive(run_id)
    notes: list[str] = []

    if archive.get("status") != "success":
        raise RuntimeError(f"{run_id} 不是成功存档（status="
                           f"{archive.get('status')!r}），不作为场景来源。")
    if archive.get("scenario") != spec["archive_scenario"]:
        raise RuntimeError(f"{run_id} 的 scenario={archive.get('scenario')!r} "
                           f"与 {scenario_id} 期望的 {spec['archive_scenario']!r} 不符。")

    # ★ 固定问题与提示词指纹必须与当前代码一致（一字都不许改）
    lm = archive.get("llm_meta") or {}
    if lm.get("question_sha256") and lm["question_sha256"] != QUESTION_SHA256:
        raise RuntimeError(
            f"{run_id} 的 question_sha256={lm['question_sha256']} 与当前固定问题"
            f"（{QUESTION_SHA256}）不一致，拒绝固化。")
    if lm.get("system_prompt_sha256") and lm["system_prompt_sha256"] != SYSTEM_PROMPT_SHA256:
        raise RuntimeError(
            f"{run_id} 的 system_prompt_sha256={lm['system_prompt_sha256']} 与当前"
            f"SYSTEM_PROMPT（{SYSTEM_PROMPT_SHA256}）不一致，拒绝固化。")

    weather, wnotes = _weather_snapshot(archive)
    knowledge, knotes = _knowledge_snapshot(archive)
    notes += wnotes + knotes

    model_version = (archive.get("model_meta") or {}).get("model_version") \
        or (archive.get("model_output") or {}).get("model_version")

    hw = archive.get("history_window") or {}
    history_window = {"start": hw.get("start"), "end": hw.get("end"),
                      "count": hw.get("count")}

    obj = {
        "scenario_id": scenario_id,
        "name": spec["name"],
        "app_version": APP_VERSION,
        "model_version": model_version,
        "analysis_time": archive.get("analysis_time"),
        "location": archive.get("location"),
        "question": QUESTION,
        "system_prompt_sha256": SYSTEM_PROMPT_SHA256,
        "question_sha256": QUESTION_SHA256,
        "current_state": archive.get("current_state"),
        "history_window": history_window,
        "history": archive.get("history"),
        "weather_evidence_snapshot": weather,
        "knowledge_evidence_snapshot": knowledge,
        # —— 回放要用的两个开关（第二遍加的，写进文件里可复核）——
        # run_scenario：B07.runs.run_analysis 的 scenario 键
        # evidence_ledger_enabled：本次回放要不要装配证据台账
        "run_scenario": spec["run_scenario"],
        "evidence_ledger_enabled": bool(spec["evidence_ledger"]),
        "source_run_id": run_id,
        "source_run_sha256": _sha256_file(run_path(run_id)),
        "source_run_created_at": archive.get("created_at"),
        "source_run_scenario": archive.get("scenario"),
        "notes": notes,
    }
    obj["scenario_sha256"] = scenario_digest(obj)
    return obj


def scenario_path(scenario_id: str) -> Path:
    return SCENARIOS_DIR / f"{scenario_id}.json"


def write_scenario(obj: dict) -> Path:
    SCENARIOS_DIR.mkdir(parents=True, exist_ok=True)
    p = scenario_path(obj["scenario_id"])
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n",
                 encoding="utf-8")
    return p


def resolve_sources(overrides: dict | None = None,
                    refresh: bool = False) -> dict[str, str | None]:
    """决定每个场景的来源 run。

    默认行为（**冻结要稳定**）：
      · 已有场景文件里记着来源 run → 继续用它（不随时间漂移）；
      · 没有 → 自动挑一条并记下来。
    `--refresh-source` 强制重新挑最新的一条。
    """
    overrides = overrides or {}
    runs = _iter_runs()
    out: dict[str, str | None] = {}
    for sid in SCENARIO_SPECS:
        if sid in overrides:
            out[sid] = overrides[sid]
            continue
        pinned = None
        p = scenario_path(sid)
        if p.exists() and not refresh:
            try:
                pinned = json.loads(p.read_text(encoding="utf-8")).get("source_run_id")
            except Exception:               # noqa: BLE001
                pinned = None
        if pinned and run_path(pinned).exists():
            out[sid] = pinned
        else:
            out[sid] = pick_source(sid, runs)
    return out


# --------------------------------------------------------------------------
# 生成 / 校验
# --------------------------------------------------------------------------

def build(overrides: dict | None = None, refresh: bool = False) -> dict:
    """生成五个场景文件。返回 {scenario_id: (path, obj)}。"""
    sources = resolve_sources(overrides, refresh)
    built = {}
    for sid in SCENARIO_SPECS:
        rid = sources.get(sid)
        if not rid:
            raise RuntimeError(
                f"找不到 {sid} 的来源存档（scenario="
                f"{SCENARIO_SPECS[sid]['archive_scenario']} 且 status=success）。")
        obj = build_scenario(sid, rid)
        built[sid] = (write_scenario(obj), obj)
    return built


def verify() -> tuple[bool, list[dict]]:
    """校验现有场景文件：指纹是否被改、内容是否仍与存档一致。"""
    rows = []
    all_ok = True
    for sid in SCENARIO_SPECS:
        p = scenario_path(sid)
        if not p.exists():
            all_ok = False
            rows.append({"scenario_id": sid, "ok": False,
                         "reason": f"场景文件不存在：{p}"})
            continue
        try:
            obj = json.loads(p.read_text(encoding="utf-8"))
        except Exception as exc:           # noqa: BLE001
            all_ok = False
            rows.append({"scenario_id": sid, "ok": False,
                         "reason": f"JSON 解析失败：{exc}"})
            continue

        problems = []
        stored = obj.get("scenario_sha256")
        recomputed = scenario_digest(obj)
        if stored != recomputed:
            problems.append(f"scenario_sha256 与文件内容不符"
                            f"（记录 {stored}，重算 {recomputed}）——文件被手改过")

        rid = obj.get("source_run_id")
        if not rid or not run_path(rid).exists():
            problems.append(f"来源存档 {rid!r} 不存在，无法与存档比对")
            rows.append({"scenario_id": sid, "ok": False,
                         "reason": "；".join(problems),
                         "source_run_id": rid,
                         "scenario_sha256": stored})
            all_ok = False
            continue

        src_sha = _sha256_file(run_path(rid))
        if obj.get("source_run_sha256") != src_sha:
            problems.append(f"来源存档 {rid} 的 sha256 已变化"
                            f"（记录 {obj.get('source_run_sha256')}，实际 {src_sha}）")

        # 与存档重新比对一遍核心字段
        try:
            fresh = build_scenario(sid, rid)
        except Exception as exc:           # noqa: BLE001
            problems.append(f"从存档重建失败：{type(exc).__name__}: {exc}")
            fresh = None
        if fresh is not None:
            for key in ("current_state", "history", "history_window",
                        "weather_evidence_snapshot", "knowledge_evidence_snapshot",
                        "analysis_time", "location", "model_version",
                        "run_scenario", "evidence_ledger_enabled"):
                if canonical_json(obj.get(key)) != canonical_json(fresh.get(key)):
                    problems.append(f"字段 {key} 与存档重建结果不一致")

        ok = not problems
        all_ok = all_ok and ok
        rows.append({
            "scenario_id": sid,
            "ok": ok,
            "reason": "；".join(problems) if problems else "一致",
            "source_run_id": rid,
            "scenario_sha256": stored,
            "recomputed_sha256": recomputed,
        })
    return all_ok, rows


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _print_build_report(built: dict) -> None:
    print("=" * 74)
    print("B08 第一遍 · 固定场景生成（从已有存档抽取，未重跑任何分析）")
    print("=" * 74)
    print(f"\n  app_version : {APP_VERSION}")
    print(f"  固定问题     : {QUESTION}")
    print(f"  question_sha : {QUESTION_SHA256}")
    print(f"  prompt_sha   : {SYSTEM_PROMPT_SHA256}")
    print(f"  输出目录     : {SCENARIOS_DIR.relative_to(_PROJECT_ROOT)}")
    for sid, (path, obj) in built.items():
        w = obj.get("weather_evidence_snapshot")
        print("\n  ── " + "─" * 66)
        print(f"  {sid} · {obj['name']}")
        print(f"    文件        : {path.relative_to(_PROJECT_ROOT)}")
        print(f"    来源 run    : {obj['source_run_id']}"
              f"（{obj.get('source_run_created_at')}）")
        print(f"    analysis_time: {obj['analysis_time']}")
        print(f"    位置        : {obj['location']}")
        print(f"    模型版本    : {obj['model_version']}")
        print(f"    历史窗口    : {obj['history_window'].get('start')}"
              f" → {obj['history_window'].get('end')}"
              f"（{obj['history_window'].get('count')} 点）")
        if w:
            print(f"    天气快照    : provider={w.get('provider')} "
                  f"is_constructed={w.get('is_constructed')} "
                  f"available={w.get('available')}")
            print(f"                  retrieved_at={w.get('retrieved_at')} "
                  f"forecast_time={w.get('forecast_time')}")
            if w.get("error"):
                print(f"                  error={w['error']}")
        else:
            print("    天气快照    : （该场景不接入外部天气）")
        k = obj.get("knowledge_evidence_snapshot")
        print(f"    知识快照    : "
              + (f"{len(k.get('citations') or [])} 条引用，"
                 f"has_relevant={k.get('has_relevant')}" if k else "（无）"))
        print(f"    scenario_sha256: {obj['scenario_sha256']}")
    print("\n" + "=" * 74)
    print(f"  ✅ 已写入 {len(built)} 个场景文件")
    print("     校验：python -m B07.scenarios_build --verify")
    print("=" * 74)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="B08 第一遍 · 固定场景抽取 / 校验（不重跑、不调 LLM）")
    ap.add_argument("--verify", action="store_true",
                    help="只校验现有场景文件是否与存档一致（不写盘）")
    ap.add_argument("--refresh-source", action="store_true",
                    help="重新挑来源 run（默认沿用已记录的来源，保证冻结不漂移）")
    ap.add_argument("--source", action="append", default=[],
                    help="手动指定来源，形如 S1=run_113（可多次）")
    args = ap.parse_args(argv)

    overrides = {}
    for item in args.source:
        if "=" not in item:
            ap.error(f"--source 需要 Sx=run_XXX 形式，收到 {item!r}")
        sid, rid = item.split("=", 1)
        sid = sid.strip().upper()
        if sid not in SCENARIO_SPECS:
            ap.error(f"未知场景 {sid!r}，可选：{sorted(SCENARIO_SPECS)}")
        overrides[sid] = rid.strip()

    if args.verify:
        ok, rows = verify()
        print("=" * 74)
        print("B08 第一遍 · 固定场景校验（--verify）")
        print("=" * 74)
        for r in rows:
            mark = "✅" if r["ok"] else "❌"
            print(f"\n  {mark} {r['scenario_id']}")
            print(f"     来源 run     : {r.get('source_run_id')}")
            print(f"     记录的指纹   : {r.get('scenario_sha256')}")
            if r.get("recomputed_sha256"):
                print(f"     重算的指纹   : {r['recomputed_sha256']}")
            print(f"     结论         : {r['reason']}")
        print("\n" + "=" * 74)
        print(f"  {'✅ 全部一致，场景文件未被改动' if ok else '❌ 存在不一致，见上'}")
        print("=" * 74)
        return 0 if ok else 1

    built = build(overrides, refresh=args.refresh_source)
    _print_build_report(built)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
