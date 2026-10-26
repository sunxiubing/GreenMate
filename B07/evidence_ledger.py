"""B07 S4-2 —— 证据台账（Evidence Ledger）

台账回答两个**不同**的问题（S4 第四步）：

  1. 这次分析**调用了哪些工具**，每个调用成功没有、返回了什么；
  2. 哪些调用结果**被本次判断采用**，升格成了证据记录。

"工具被调用过" ≠ "工具结果一定被本次判断采用" ——
所以本模块分成两层：

  - **调用台账**（ToolLedger）：每一次调用都记，包括没被采用的；
  - **证据记录**：只有被本次判断采用的才从调用台账里"升格"上来，
    才有 evidence_id / direction / adopted 这些字段可言。

方向（direction）是**证据的属性**，由本模块的程序规则给出，**不经过 LLM**。
规则的唯一出处是 `judge_direction()` —— 任何别处都不许再写一套方向判定。

每条证据记录：

    {evidence_id, source_type, source, time, status, result, error,
     direction, ext, adopted}

  - source_type ∈ {B02, B03, B04, B06}：模块级身份（台账里的口径）。
    S4-0 证据层自己的细分种类（b03_weather / b04_knowledge / ...）
    原样保留在 ext["registry_source_type"] 里，不丢。
  - direction ∈ {supporting, opposing, undetermined}
  - adopted：这条结果是否被本次判断采用

硬约束：
  - 复用 S4-0 的 make_tool_result / make_evidence / ToolLedger，不另起一套
  - 方向判定是程序规则，**不得由 LLM 决定**
  - 不把 NORMAL 当作"安全"的证明（见 limitations_of）
  - 不改 B01 / B02 / B03 / B04 / B05 / B06 任何文件
  - 本模块本身不联网、不调 LLM

用法：

    from B07.evidence_ledger import EvidenceLedger, judge_direction

    ledger = EvidenceLedger()
    ledger.record_call("B02.get_environment_history", {"start": ..., "end": ...})
    entry = ledger.add_evidence(tool_result, "B02", ext={...},
                                tool_name="B02.get_environment_history")
    print(entry["direction"])
    print(ledger.adoption_summary()["text"])
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from B07.evidence_registry import (  # noqa: F401  (S4-0 的原语，不另起一套)
    EVIDENCE_STATUS_ERROR,
    EVIDENCE_STATUS_OK,
    EVIDENCE_STATUS_UNAVAILABLE,
    EvidenceValidationError,
    ToolLedger,
    make_evidence,
    make_tool_result,
)
from B07.evidence_registry import _digest_of as _digest

# --------------------------------------------------------------------------
# 常量
# --------------------------------------------------------------------------

#: 台账口径的 source_type（模块级身份）
SOURCE_TYPE_B02 = "B02"
SOURCE_TYPE_B03 = "B03"
SOURCE_TYPE_B04 = "B04"
SOURCE_TYPE_B06 = "B06"

SOURCE_TYPES = (SOURCE_TYPE_B02, SOURCE_TYPE_B03,
                SOURCE_TYPE_B04, SOURCE_TYPE_B06)

#: 方向取值
DIRECTION_SUPPORTING = "supporting"      # 支持提高关注
DIRECTION_OPPOSING = "opposing"           # 提示可能缓解或反向
DIRECTION_UNDETERMINED = "undetermined"  # 仍不足以确定

DIRECTIONS = (DIRECTION_SUPPORTING, DIRECTION_OPPOSING, DIRECTION_UNDETERMINED)

#: 末段升温的取点数（窗口末尾几个点算"末段"）
TAIL_POINTS = 3


# --------------------------------------------------------------------------
# 方向判定（唯一出处，纯程序规则）
# --------------------------------------------------------------------------

def tail_rising(temps: list, points: int = TAIL_POINTS) -> bool:
    """末段是否升温。

    规则：窗口末尾 points 个点温度**单调不降**，且末点严格高于该段首点。

    写成函数而不是内联表达式，是为了让它只有一个出处 ——
    方向判定和摘要文字引用的是同一个判断。
    """
    if not temps or len(temps) < points:
        return False
    tail = list(temps[-points:])
    for i in range(len(tail) - 1):
        if tail[i + 1] < tail[i]:
            return False
    return tail[-1] > tail[0]


def judge_b02_direction(result: dict) -> str:
    """历史（B02）方向。

    稳定判据未通过 **且** 末段升温 → supporting；否则 undetermined。
    （稳定判据通过 = 没发现异常，不构成"支持提高关注"的理由。）
    """
    result = result or {}
    passed = result.get("stability_passed")
    rising = tail_rising(result.get("temps") or [])
    if passed is False and rising:
        return DIRECTION_SUPPORTING
    return DIRECTION_UNDETERMINED


def judge_b03_direction(result: dict) -> str:
    """外部天气（B03）方向。signal 由 B07.weather 判定，本模块不重算。"""
    from B07.weather import (
        SIGNAL_DETERIORATING,
        SIGNAL_IMPROVING,
        SIGNAL_STABLE,
    )

    signal = (result or {}).get("signal")
    if signal == SIGNAL_DETERIORATING:
        return DIRECTION_SUPPORTING
    if signal == SIGNAL_IMPROVING:
        return DIRECTION_OPPOSING
    if signal == SIGNAL_STABLE:
        return DIRECTION_UNDETERMINED
    return DIRECTION_UNDETERMINED


def judge_b04_direction(result: dict) -> str:
    """知识（B04）方向。

    知识证据提供的是"决策基于不完整信息可能导致作物受损"这一**支持依据**，
    所以方向是 supporting。它自己的限定（不能证明未来一定恶化）
    不属于方向，放在 limitations_of() 里。
    """
    return DIRECTION_SUPPORTING


def judge_b06_direction(result: dict) -> str:
    """模型（B06）方向。

    WATCH → supporting；NORMAL → opposing。
    NORMAL 不等于"确认无异常"（模型 recall 偏低，存在漏报），
    这一限定写在 limitations_of() 里，不改变方向本身。
    """
    label = (result or {}).get("predicted_attention")
    if label == "WATCH":
        return DIRECTION_SUPPORTING
    if label == "NORMAL":
        return DIRECTION_OPPOSING
    return DIRECTION_UNDETERMINED


def judge_direction(entry: dict) -> str:
    """给一条证据记录判方向 —— **方向判定的唯一出处**。

    规则（全部是程序规则，不经过 LLM）：

      任何 status != "ok" 的证据            → undetermined
      B02 历史：稳定判据未通过 且 末段升温  → supporting；否则 undetermined
      B06 模型：WATCH → supporting；NORMAL → opposing
      B03 天气：external_deteriorating → supporting；
                 external_improving     → opposing；
                 external_stable        → undetermined
      B04 知识：                          → supporting
    """
    if (entry.get("status") or "") != EVIDENCE_STATUS_OK:
        return DIRECTION_UNDETERMINED

    source_type = entry.get("source_type")
    result = entry.get("result") or {}

    if source_type == SOURCE_TYPE_B02:
        return judge_b02_direction(result)
    if source_type == SOURCE_TYPE_B03:
        return judge_b03_direction(result)
    if source_type == SOURCE_TYPE_B04:
        return judge_b04_direction(result)
    if source_type == SOURCE_TYPE_B06:
        return judge_b06_direction(result)
    return DIRECTION_UNDETERMINED


# --------------------------------------------------------------------------
# 摘要与依据（与方向规则同源，避免两处漂移）
# --------------------------------------------------------------------------

def summarize(entry: dict) -> str:
    """一句话内容摘要（报告里的"内容摘要"列）。"""
    source_type = entry.get("source_type")
    result = entry.get("result") or {}

    if source_type == SOURCE_TYPE_B02:
        passed = result.get("stability_passed")
        rising = tail_rising(result.get("temps") or [])
        state = "稳定" if passed else "不稳定"
        tail = "末段温度上升" if rising else "末段温度未上升"
        return f"内部状态{state}、{tail}"

    if source_type == SOURCE_TYPE_B06:
        return (f"模型输出 {result.get('predicted_attention')}"
                f"（confidence={result.get('confidence')}, "
                f"model_version={result.get('model_version')}）")

    if source_type == SOURCE_TYPE_B03:
        # S5-1：证据**没取到**时，摘要不许写成"判定为 None" ——
        # 那像是做过判定。取不到就说取不到。
        if (entry.get("status") or "") != EVIDENCE_STATUS_OK:
            return f"外部条件证据不可用（status={entry.get('status')}）"
        return (f"外部条件判定 {result.get('signal')}"
                f"（{result.get('judge_method')}）")

    if source_type == SOURCE_TYPE_B04:
        citations = result.get("citations") or []
        top = citations[0] if citations else {}
        return (f"知识检索 {result.get('query')!r} 命中 {len(citations)} 条"
                f"，主命中 {top.get('chunk_id')}（score={top.get('score')}）")

    return "（未知来源）"


def basis_of(entry: dict) -> str:
    """为什么是 supporting / opposing —— 依据句。"""
    source_type = entry.get("source_type")
    result = entry.get("result") or {}

    if source_type == SOURCE_TYPE_B02:
        return (f"稳定判据未通过（{result.get('stability_conclusion')}）"
                f"且末段温度上升")

    if source_type == SOURCE_TYPE_B06:
        return f"B06 在该窗口上真实输出 {result.get('predicted_attention')}"

    if source_type == SOURCE_TYPE_B03:
        return f"外部条件判定为 {result.get('signal')}：{result.get('basis')}"

    if source_type == SOURCE_TYPE_B04:
        return ("该条目提供『决策基于不完整信息可能导致作物受损』这一支持依据")

    return ""


def undetermined_reason(entry: dict) -> str:
    """方向为 undetermined 的证据，为什么还不足以确定。"""
    source_type = entry.get("source_type")
    result = entry.get("result") or {}

    if source_type == SOURCE_TYPE_B02:
        return ("稳定判据未通过且末段升温的联合条件不成立，"
                "内部历史不足以支持提高关注")

    if source_type == SOURCE_TYPE_B03:
        # S5-1：没取到 ≠ 判成了"既不恶化也不缓解"。
        if (entry.get("status") or "") != EVIDENCE_STATUS_OK:
            return (f"外部条件证据当前不可用"
                    f"（status={entry.get('status')}）"
                    f"，没有外部方向可判，不改变关注判断")
        return (f"外部条件判定为 {result.get('signal')}，"
                f"既不构成恶化也不构成缓解，不改变关注判断")

    if source_type == SOURCE_TYPE_B06:
        return (f"模型输出 {result.get('predicted_attention')}，"
                f"既不属于 WATCH 也不属于 NORMAL，方向无法判定")

    return "该来源的证据不足以判定方向"


def limitations_of(entry: dict) -> list[dict]:
    """这条证据**仍不足以确定**的具体限定（每条都带来源归属）。

    这些是"判断项"，不是 direction —— 知识证据方向是 supporting，
    但它同时提供一条"不能证明一定恶化"的限定；这条限定进 undetermined 列表。
    """
    source_type = entry.get("source_type")
    result = entry.get("result") or {}
    ext = entry.get("ext") or {}
    out: list[dict] = []

    if source_type == SOURCE_TYPE_B04:
        out.append({
            "summary": "知识证据只支持『需要关注』",
            "reason": ("只支持『要关注』，不能证明未来一定恶化 —— "
                       "该条目是方法论提示，不是本温室的实测结论"),
        })

    if source_type == SOURCE_TYPE_B06 and result.get("predicted_attention") == "NORMAL":
        metrics = ext.get("metrics") or {}
        recall = metrics.get("recall")
        f1 = metrics.get("f1")
        if recall is not None and f1 is not None:
            perf = f"该模型 recall={recall:.3f} / f1={f1:.3f}（见 B06 模型卡）"
        else:
            perf = "该模型在 B06 模型卡上存在漏报（recall 偏低）"
        out.append({
            "summary": "模型输出 NORMAL",
            "reason": (f"{perf}，存在漏报，NORMAL 不等于确认无异常"),
        })

    # S5-1：这条限定说的是"取到的那一点"的口径。
    # 证据**没取到**时根本没有点，这条限定不适用（留着会变成误导）。
    if source_type == SOURCE_TYPE_B03 and \
            (entry.get("status") or "") == EVIDENCE_STATUS_OK:
        out.append({
            "summary": "外部天气取的是窗口末时刻单点值",
            "reason": "取的是窗口末时刻单点值而非窗口极值，窗口内出现过的更高/更低值可能被漏掉",
        })

    if (entry.get("status") or "") != EVIDENCE_STATUS_OK:
        out.append({
            "summary": f"证据源不可用（status={entry.get('status')}）",
            "reason": entry.get("error") or "该来源未返回可用结果",
        })

    return out


# --------------------------------------------------------------------------
# 证据台账
# --------------------------------------------------------------------------

class EvidenceLedger:
    """本次分析的证据台账。

    两层结构：
      - 调用台账（ToolLedger）：每次调用都记，含未被采用的；
      - 证据记录：被本次判断采用的调用结果，才升格成带 direction 的记录。
    """

    def __init__(self) -> None:
        self._tool_ledger = ToolLedger()
        self._entries: list[dict] = []
        self._reasons: dict[str, str] = {}

    # ---- 调用台账 ----

    def record_call(self, tool_name: str, arguments=None,
                    status: str = EVIDENCE_STATUS_OK, purpose: str | None = None,
                    result_digest: str | None = None,
                    started_at=None, finished_at=None,
                    duration_ms=None, error: str | None = None) -> dict:
        """记一次工具调用（无论结果会不会被采用）。

        S5-1：status != ok 时必须把失败原因（error）写进台账 ——
        "调用失败"必须说得清为什么失败。
        """
        rec = self._tool_ledger.record(
            tool_name, arguments=arguments, status=status,
            started_at=started_at, finished_at=finished_at,
            duration_ms=duration_ms, result_digest=result_digest,
            error=error,
        )
        rec["purpose"] = purpose
        return rec

    def mark_not_adopted(self, tool_name: str, reason: str) -> None:
        """登记"这个调用为什么没被采用"。"""
        self._reasons[tool_name] = reason

    # ---- 证据记录 ----

    def add_evidence(self, tool_result: dict, source_type: str, ext=None,
                     tool_name: str | None = None, adopted: bool = True,
                     evidence_id: str | None = None,
                     result_digest: str | None = None) -> dict:
        """把一份工具结果升格成证据记录。

        Args:
            tool_result: make_tool_result() 的产物。
            source_type: B02 / B03 / B04 / B06。
            ext:         扩展信息（provider / model_version / citation ...）。
            tool_name:   对应的调用记录名；给了就把那次调用标为"被采用"。
            adopted:     False = 记下来了，但**没被本次判断采用**。
            evidence_id: 缺省按 "EV-<source_type>-<序号>" 生成。

        Returns:
            证据记录 dict。
        """
        if source_type not in SOURCE_TYPES:
            raise EvidenceValidationError(
                f"source_type 必须是 {SOURCE_TYPES} 之一，收到 {source_type!r}"
            )

        ev = make_evidence(tool_result, source_type, ext)

        if evidence_id is None:
            seq = 1 + len([e for e in self._entries
                           if e["source_type"] == source_type])
            evidence_id = f"EV-{source_type}-{seq:02d}"

        entry = {
            "evidence_id": evidence_id,
            "source_type": ev["source_type"],
            "source": ev["source"],
            "time": ev["time"],
            "status": ev["status"],
            "result": ev["result"],
            "error": ev["error"],
            "ext": ev["ext"],
            "adopted": bool(adopted),
        }
        entry["direction"] = judge_direction(entry)
        self._entries.append(entry)

        if tool_name:
            self._tool_ledger.adopt(tool_name, result_digest=result_digest)

        return entry

    # ---- 查询 ----

    @property
    def entries(self) -> list[dict]:
        """全部证据记录（含未采用的）。"""
        return list(self._entries)

    def adopted_entries(self) -> list[dict]:
        """被本次判断采用的证据记录 —— 进分歧三列表的就是这些。"""
        return [e for e in self._entries if e["adopted"]]

    def entries_by_source_type(self, source_type: str) -> list[dict]:
        return [e for e in self._entries if e["source_type"] == source_type]

    def tool_ledger(self) -> ToolLedger:
        return self._tool_ledger

    def adoption_summary(self) -> dict:
        """"调用了 N 个工具，其中 M 条结果被采用为证据"。

        同时列出没被采用的是哪一条、为什么没采用。
        """
        records = self._tool_ledger.to_dict()["records"]
        not_adopted = []
        for rec in records:
            if rec.get("adopted"):
                continue
            not_adopted.append({
                "tool_name": rec.get("tool_name"),
                "status": rec.get("status"),
                "purpose": rec.get("purpose"),
                "arguments": rec.get("arguments"),
                "reason": self._reasons.get(rec.get("tool_name"),
                                            "未声明采用原因"),
            })
        total = len(records)
        adopted = sum(1 for r in records if r.get("adopted"))
        return {
            "total_calls": total,
            "adopted_calls": adopted,
            "not_adopted_calls": not_adopted,
            "text": f"本次调用了 {total} 个工具，其中 {adopted} 条结果被采用为证据",
        }

    # ---- 序列化 ----

    def to_dict(self) -> dict:
        """可序列化视图（存档用）。"""
        return {
            "entries": [dict(e) for e in self._entries],
            "tool_calls": self._tool_ledger.to_dict(),
            "not_adopted_reasons": dict(self._reasons),
            "adoption": self.adoption_summary(),
            "counts": {
                "entries": len(self._entries),
                "adopted_entries": len(self.adopted_entries()),
            },
        }

    @classmethod
    def from_dict(cls, data: dict) -> "EvidenceLedger":
        """从存档还原台账。

        **direction 一律重新按规则算**，不复读存档里的字符串 ——
        这样"规则改了"会立刻表现为复算不一致，而不是被存档里的旧值掩盖。
        """
        ledger = cls()
        for entry in (data or {}).get("entries") or []:
            restored = dict(entry)
            restored["direction"] = judge_direction(restored)
            ledger._entries.append(restored)

        tool_calls = (data or {}).get("tool_calls") or {}
        for rec in tool_calls.get("records") or []:
            ledger._tool_ledger._records.append(dict(rec))

        ledger._reasons = dict((data or {}).get("not_adopted_reasons") or {})
        return ledger


# --------------------------------------------------------------------------
# 便捷函数：给外部模块用
# --------------------------------------------------------------------------

def ledger_entries(ledger) -> list[dict]:
    """从台账对象或台账 dict 里取出证据记录列表。"""
    if ledger is None:
        return []
    if isinstance(ledger, EvidenceLedger):
        return ledger.entries
    if isinstance(ledger, dict):
        return list(ledger.get("entries") or [])
    entries = getattr(ledger, "entries", None)
    if callable(entries):
        return list(entries())
    return list(entries or [])


def adopted_ledger_entries(ledger) -> list[dict]:
    """从台账对象或台账 dict 里取出**被采用**的证据记录。"""
    if isinstance(ledger, EvidenceLedger):
        return ledger.adopted_entries()
    if isinstance(ledger, dict):
        return [e for e in (ledger.get("entries") or []) if e.get("adopted")]
    entries = getattr(ledger, "adopted_entries", None)
    if callable(entries):
        return list(entries())
    return [e for e in ledger_entries(ledger) if e.get("adopted")]


def digest_of(payload) -> str:
    """结果摘要（与 S4-0 台账用的是同一份实现）。"""
    return _digest(payload)


def weather_context_from_ledger(ledger) -> dict | None:
    """从台账里取 B03 那条证据，导出外部条件上下文 + 证据身份。

    补充1：external_conditions（第 6 个字段）必须**从台账派生**，
    不允许在 judgement.py 里另写一套天气字段的组装逻辑 ——
    否则同一份证据的身份会在两处定义，日后必然漂移。

    S5-1（证据退化）：B03 证据**存在但不可用**时，外部条件仍然要表达
    "不可用"（provider 保留、forecast_time=None、status=unavailable），
    **不许整块消失** —— 消失会让下游以为"没有外部信息"，
    而不是"外部信息不可用"，这正是 S5 要区分的两种情况。

    Returns:
        {"context": <build_external_context 的原样返回>,
         "provider": ..., "forecast_time": ..., "evidence_id": ...,
         "status": "ok"|"unavailable"|"error"}
        台账里**根本没有** B03 证据时返回 None（那种情况才整块不出现）。
    """
    entries = adopted_ledger_entries(ledger)
    b03 = [e for e in entries if e.get("source_type") == SOURCE_TYPE_B03]
    if not b03:
        b03 = [e for e in ledger_entries(ledger)
               if e.get("source_type") == SOURCE_TYPE_B03]
    if not b03:
        return None

    entry = b03[0]
    ext = entry.get("ext") or {}
    status = entry.get("status") or EVIDENCE_STATUS_OK
    context = entry.get("result")

    if status != EVIDENCE_STATUS_OK:
        # 证据不可用：结果为 None（不许留上次成功的值），
        # 但"为什么不可用"必须从台账里带出来。
        unavailable = ext.get("unavailable_context") or {}
        error = entry.get("error") or unavailable.get("error")
        ctx = {
            "signal": unavailable.get("signal") or "external_unavailable",
            "judge_method": unavailable.get("judge_method") or "unavailable",
            "basis": unavailable.get("basis") or error,
            "baseline_point": None,
            "forecast_point": None,
            "precip_delta": None,
            "temp_delta": None,
            "cloud_delta": None,
            "solar_radiation": None,
            "available": False,
            "future_ok": False,
            "warnings": list(unavailable.get("warnings") or []) or (
                [error] if error else []),
            "caveats": list(unavailable.get("caveats") or []),
            "location": unavailable.get("location") or ext.get("location"),
            "forecast_window": (unavailable.get("forecast_window")
                                or ext.get("forecast_window")),
            "analysis_time": unavailable.get("analysis_time"),
            "status": status,
            "error": error,
        }
        return {
            "context": ctx,
            "provider": ext.get("provider"),
            "forecast_time": None,
            "evidence_id": entry.get("evidence_id"),
            "status": status,
        }

    if not isinstance(context, dict) or not context:
        return None

    return {
        "context": context,
        "provider": ext.get("provider"),
        "forecast_time": ext.get("forecast_time"),
        "evidence_id": entry.get("evidence_id"),
        "status": status,
    }


#: 证据表的列（报告与回溯共用同一份表头）
EVIDENCE_TABLE_HEADER = ("| # | source_type | 来源模块 | time | status | "
                         "direction | 内容摘要 | adopted |")


def format_evidence_table(ledger) -> list[str]:
    """把台账渲染成证据表（Markdown 表格行）。

    只有**被本次判断采用**的证据进表；没被采用的调用在"未采用"清单里说明。
    """
    rows = [EVIDENCE_TABLE_HEADER]
    for i, entry in enumerate(adopted_ledger_entries(ledger), 1):
        rows.append(
            f"| {i} "
            f"| {entry.get('source_type')} "
            f"| {entry.get('source')} "
            f"| {entry.get('time')} "
            f"| {entry.get('status')} "
            f"| {entry.get('direction')} "
            f"| {summarize(entry)} "
            f"| {'是' if entry.get('adopted') else '否'} |"
        )
    return rows
