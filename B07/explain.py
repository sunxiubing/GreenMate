"""B07 Step 1（第四步 B）— LLM 解释（只解释，不判断）

职责：
  把 B07/judgement.py 产出的结构化判断交给 LLM，生成一段人话解释。

与结构化判断的关系：
  - 结构化判断（judgement.py）是程序算出来的，不经过 LLM
  - 本模块只负责"解释"，不产生也不修改任何判断结论
  - LLM 调用失败必须显式报错，不许用静态文案伪装成功
  - 解释里出现的数字若在证据集合里找不到，会被一致性校验记为 warning

硬约束：
  - 不修改 B01 任何文件（复用 B01.llm.get_client / describe_exception）
  - 导入 B01.llm 会自动 load B01/.env，这里不自己读 .env

用法：
  python B07/explain.py --anchor 2026-10-21T16:00:00
"""

import os
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))


# --------------------------------------------------------------------------
# 规则常量（模块顶部）
# --------------------------------------------------------------------------
FIXED_QUESTION = "分析当前番茄生长环境状态以及接下来值得关注的变化，并说明判断依据。"

MAX_ANSWER_CHARS = 250            # 回答字数上限
DEBUG_PREVIEW_CHARS = 50           # 调试打印的前 N 字

# 结论冲突检测：结构化判断为 NORMAL 时，这些表述属于"要求行动"
CONFLICT_PHRASES = ["需要立即处理", "立即干预", "建议马上", "需要马上", "立刻处理"]

# 事实越界检测
ORDINAL_ALLOWLIST = {0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10}   # 序数/小整数，放行
STRUCTURAL_ALLOWLIST = {24, 30, 60, 100}                # 小时/分钟/秒/百分比基数，放行
NUMBER_PATTERN = re.compile(r"\d+(?:\.\d+)?")
TIME_PATTERN = re.compile(r"\d{1,2}\s*:\s*\d{2}")
DATE_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}")
MODULE_CODE_PATTERN = re.compile(r"B0[1-9](?![0-9])", re.IGNORECASE)
VERSION_TOKEN_PATTERN = re.compile(r"b0\d-\d{8}-v\d+", re.IGNORECASE)
CN_DATE_PATTERN = re.compile(r"\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日")
CN_DATE_PART_PATTERN = re.compile(r"\d{1,2}\s*[年月日]")


SYSTEM_PROMPT = """你是 GreenMate 大棚监测系统的助手。你的任务是把下方【结构化判断结果】翻译成一段给种植者看的中文说明。

回答要求：
1. 必须引用上下文里给出的具体数值（温度、湿度、光照、波动幅度、模型版本等），不要泛泛而谈。
2. 明确说明本次判断依据了哪几项数据。
3. 绝对不得编造上下文中没有的事实——不许出现真实天气、真实传感器读数，或任何上下文里没出现过的数字与结论。上下文里没有的信息，就不要说。
4. 如果数据不足或状态稳定，允许直接说"目前没有足够依据提高关注程度"，不要为了显得智能而制造问题。
5. 用中文回答，控制在 250 字以内，输出纯文本，不要使用 Markdown 标记（**加粗**、# 标题、``` 代码块等）。
"""


# --------------------------------------------------------------------------
# 上下文拼装
# --------------------------------------------------------------------------

def format_judgement_context(run_bundle: dict) -> str:
    """把五块内容拼成 LLM 上下文，顺序固定。

    ① 当前环境   ② 最近历史主要变化   ③ B06 短时预测
    ④ 结构化判断结果   ⑤ 当前限制条件
    """
    from B07.judgement import build_judgement

    judgement = run_bundle.get("judgement") or build_judgement(run_bundle)

    cs = judgement["current_environment"]
    rt = judgement["recent_trend"]
    mo = run_bundle.get("model_output") or {}
    es = judgement["evidence_status"]
    lim = judgement["limitations"]

    blocks: list[str] = []

    # ① 当前环境
    blocks.append(
        "【① 当前环境】\n"
        f"采样时间戳：{cs['sampled_at']}\n"
        f"温度：{cs['temperature']} {cs['unit']['temperature']}\n"
        f"湿度：{cs['humidity']} {cs['unit']['humidity']}\n"
        f"光照：{cs['lux']} {cs['unit']['lux']}\n"
        f"数据来源 source_type：{cs['source_type']}\n"
        f"模拟场景标签：{cs['sim_scenario']}"
    )

    # ② 最近历史主要变化（引用稳定判据里的具体数值）
    crit_lines = "\n".join(
        f"  - {name}：实测={c['value']}，阈值={c['threshold']}，"
        f"{'通过' if c['passed'] else '未通过'}（{c['note']}）"
        for name, c in rt["criteria"].items()
    )
    blocks.append(
        "【② 最近历史主要变化】\n"
        f"窗口区间：{rt['window_start']} → {rt['window_end']}"
        f"（{rt['window_span']}）\n"
        f"稳定性判定：{rt['stability_verdict']}\n"
        f"稳定判据明细：\n{crit_lines}\n"
        f"稳定判据结论：{rt['conclusion']}"
    )

    # ③ B06 短时预测
    blocks.append(
        "【③ B06 短时预测】\n"
        f"predicted_attention：{mo.get('predicted_attention')}\n"
        f"model_version：{mo.get('model_version')}\n"
        f"confidence：{mo.get('confidence')}"
        f"（NORMAL {mo.get('confidence_normal')} / WATCH {mo.get('confidence_watch')}）\n"
        f"窗口 ID：{mo.get('window_id')}\n"
        f"DTW 相似度：温度 {mo.get('dtw_temperature')}，"
        f"湿度 {mo.get('dtw_humidity')}，光照 {mo.get('dtw_lux')}"
    )

    # ④ 结构化判断结果（五字段原样列出）
    # 注意（S5-1 撤回过一次）：④ 块**不列** external_conditions。
    # S3 的不变量是"带/不带外部天气两次运行只有 ⑥ 块不同"，
    # S4 的不变量是"⑦ 块是纯追加"—— ④ 块一旦带上外部条件，
    # 这两条不变量就都被破坏了。外部条件只出现在 ⑥ 块里。
    blocks.append(
        "【④ 结构化判断结果】\n"
        f"current_environment：温度 {cs['temperature']}℃，湿度 {cs['humidity']}%，"
        f"光照 {cs['lux']} lux，来源 {cs['source_type']}，采样于 {cs['sampled_at']}\n"
        f"recent_trend：{rt['stability_verdict']}；{rt['conclusion']}\n"
        f"predicted_attention：{judgement['predicted_attention']}\n"
        f"evidence_status：{es['summary']}；证据项："
        + "、".join(f"[{i['module']}]{i['item']}" for i in es["items"]) + "\n"
        f"limitations：\n"
        + "\n".join(f"  - {lim[k]['text']}" for k in lim["all_keys"])
    )

    # ⑤ 当前限制条件（原文）
    blocks.append(
        "【⑤ 当前限制条件（limitations 原文）】\n"
        + "\n".join(f"  - {lim[k]['text']}" for k in lim["all_keys"])
    )

    # ⑥ 外部条件（S3-1；没有外部条件时这一块整个不出现）
    ext = judgement.get("external_conditions")
    if ext:
        blocks.append(_format_external_block(ext))

    # ⑦ 证据分歧（S4-2；没有证据台账时这一块整个不出现）
    div = judgement.get("evidence_divergence")
    if div:
        blocks.append(_format_divergence_block(
            div, run_bundle.get("evidence_ledger")))

    return "\n\n".join(blocks)


def _format_divergence_block(div: dict, ledger=None) -> str:
    """把 evidence_divergence 拼成一段结构化文本（并入 state，不改提示词）。

    规矩（S4-2）：
      - 分方向列出，每条都带来源标识（source_type + 来源 + 时间）；
      - **不给仲裁后的单一答案** —— 不投票、不"谁多听谁"；
      - **不写引导性措辞** —— 只做中性陈述，不出现"请务必提到分歧"
        "请综合判断"这类指令（提示词不能改，state 里也不许用引导句）。
    """
    each_source = div.get("each_source") or {}
    dist = "、".join(f"{k}={v}" for k, v in each_source.items()) or "（无）"

    lines = [
        "【⑦ 证据分歧】",
        f"各来源方向：{dist}",
    ]
    if div.get("has_conflict"):
        lines.append(
            f"方向不一致：存在（支持 {len(div.get('supporting') or [])} 条 / "
            f"相反 {len(div.get('opposing') or [])} 条）"
        )
    else:
        lines.append("方向不一致：不存在")

    def _items(title, items, key, label):
        lines.append(f"{title}：")
        if not items:
            lines.append("  - （无）")
            return
        for it in items:
            lines.append(
                f"  - [{it.get('source_type')}] "
                f"来源 {it.get('source')} ｜ 时间 {it.get('time')}"
            )
            lines.append(f"    摘要：{it.get('summary')}")
            lines.append(f"    {label}：{it.get(key)}")

    _items("支持提高关注的证据", div.get("supporting") or [], "basis", "依据")
    _items("提示可能缓解或反向的证据", div.get("opposing") or [], "basis", "依据")
    _items("当前仍不足以确定的部分", div.get("undetermined") or [], "reason",
           "为什么仍不足以确定")

    # S5-1：当前不可用的证据单独一节（来源 / status / 失败原因）。
    # 这几条**不进**支持/相反两列 —— 不可用的证据不参与方向判定。
    if ledger is not None:
        lines.extend(_unavailable_evidence_lines(ledger))

    lines.append(f"分歧处理：{div.get('resolution')}")

    return "\n".join(lines)


def _unavailable_evidence_lines(ledger) -> list[str]:
    """⑦ 块里"当前不可用的证据"一节的内容。

    规矩（S5-1）：
      - 只列**被本次判断采用**、但 status != ok 的证据；
      - 每条都带来源标识、status、失败原因；
      - 说明这些证据不参与方向判定（不是"方向不明"，是"没取到"）。
    """
    from B07.evidence_ledger import adopted_ledger_entries

    entries = adopted_ledger_entries(ledger)
    bad = [e for e in entries if (e.get("status") or "") != "ok"]

    lines = ["当前不可用的证据（取不到，不参与方向判定，不进上面三列）："]
    if not bad:
        lines.append("  - （无）")
        return lines

    for e in bad:
        lines.append(
            f"  - [{e.get('source_type')}] "
            f"来源 {e.get('source')} ｜ 时间 {e.get('time')}"
        )
        lines.append(f"    status：{e.get('status')}")
        lines.append(f"    失败原因：{e.get('error') or '（未记录原因）'}")
        lines.append(
            "    说明：该证据当前不可用，与它相关的判断受到限制；"
            "其余证据不受影响，仍可照常判断。"
        )
    return lines


def _format_external_block(ext: dict) -> str:
    """把 external_conditions 拼成一段结构化文本（并入 state，不改提示词）。"""
    if not ext.get("available"):
        # 外部条件不可用：必须明说，绝不能让 LLM 凭空编造天气
        reasons = []
        for w in ext.get("warnings") or []:
            reasons.append(f"  - {w}")
        if ext.get("forecast_point") or ext.get("baseline_point"):
            # 数据取到了，但没通过"在 analysis_time 之后"这道门槛
            return (
                "【⑥ 外部天气条件】\n"
                "外部天气数据取到了，但本次不产生外部条件判定"
                "（预报时刻未位于 analysis_time 之后，不能当作未来天气）。\n"
                "外部天气情况：本次不可用（没有可用的外部条件结论）。\n"
                "说明：上下文里没有可用的外部天气结论，"
                "不要编造天气、不要提及任何具体气象数字。\n"
                "原因：\n" + ("\n".join(reasons) if reasons else "  - （未记录原因）")
            )
        return (
            "【⑥ 外部天气条件】\n"
            f"外部天气证据状态：{ext.get('status') or 'unavailable'}\n"
            f"不可用的来源（provider）：{ext.get('provider') or '（未记录）'}\n"
            f"外部条件判定：{ext.get('signal') or 'external_unavailable'}"
            f"（判定方式：{ext.get('judge_method') or 'unavailable'}）\n"
            "外部天气情况：**外部天气证据当前不可用** —— "
            "因此与外部天气影响相关的判断受到限制。\n"
            "说明：上下文里没有任何外部天气数值，不要编造天气、"
            "不要提及任何具体气象数字。\n"
            "外部天气证据“不可用”与“没有外部信息”是两件不同的事："
            "前者是这一次取数失败（status=unavailable），后者是本次不接外部证据。\n"
            "回答要求（必答）：必须在回答里明确说明"
            "“外部天气证据当前不可用”，"
            "并说明与外部天气影响相关的判断受到限制；"
            "不要给出任何外部天气的数值或方向。\n"
            "不受影响的部分：当前环境（①）、最近历史主要变化（②）、"
            "B06 短时预测（③）、限制条件（⑤）仍在上下文里，"
            "可以照常给出判断。\n"
            "失败原因：\n" + ("\n".join(reasons) if reasons else "  - （未记录原因）")
        )

    base = ext.get("baseline_point") or {}
    fore = ext.get("forecast_point") or {}

    def _num(v, unit=""):
        return "未获取" if v is None else f"{v}{unit}"

    lines = [
        "【⑥ 外部天气条件（真实公开数据）】",
        f"外部条件判定：{ext.get('signal')}",
        f"判定方式：{ext.get('judge_method')}",
        f"判定依据：{ext.get('basis')}",
        f"基线点（当前实况）时刻：{base.get('timestamp')}",
        f"  降水概率 {_num(base.get('precipitation_probability'), '%')}，"
        f"温度 {_num(base.get('temperature'), '℃')}，"
        f"云量 {_num(base.get('cloud_cover'), '%')}，"
        f"天气「{base.get('weather_description')}」",
        f"预报点（未来窗口）时刻：{fore.get('timestamp')}",
        f"  降水概率 {_num(fore.get('precipitation_probability'), '%')}，"
        f"温度 {_num(fore.get('temperature'), '℃')}，"
        f"云量 {_num(fore.get('cloud_cover'), '%')}，"
        f"太阳辐射 {_num(ext.get('solar_radiation'), ' W/m²')}，"
        f"天气「{fore.get('weather_description')}」",
        f"变化量：降水概率 Δ{_num(ext.get('precip_delta'), ' 个百分点')}，"
        f"温度 Δ{_num(ext.get('temp_delta'), '℃')}，"
        f"云量 Δ{_num(ext.get('cloud_delta'), '%')}",
        "外部条件限制（引用外部条件时必须一并说明）：",
    ]
    for c in ext.get("caveats") or []:
        lines.append(f"  - {c}")

    return "\n".join(lines)


# --------------------------------------------------------------------------
# LLM 调用
# --------------------------------------------------------------------------

class ExplainError(RuntimeError):
    """LLM 解释环节失败。显式报错，不用静态文案伪装成功。"""


def _sha12(text: str) -> str:
    """文本的 sha256 前 12 位（提示词指纹）。"""
    import hashlib
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def _usage_of(resp) -> dict:
    """把 LLM 响应的 usage 整成可存档的 dict。

    S4-3 用它做"唯一变量"的机器判定：百炼的隐式上下文缓存按**前缀逐字节**
    匹配，两组 cached_tokens 相同 ⇒ 共享前缀逐字节相同。

    字段缺失就留 None，**不补零、不猜**。
    """
    usage = getattr(resp, "usage", None)
    out = {
        "prompt_tokens": None,
        "completion_tokens": None,
        "total_tokens": None,
        "cached_tokens": None,
    }
    if usage is None:
        return out
    out["prompt_tokens"] = getattr(usage, "prompt_tokens", None)
    out["completion_tokens"] = getattr(usage, "completion_tokens", None)
    out["total_tokens"] = getattr(usage, "total_tokens", None)
    details = getattr(usage, "prompt_tokens_details", None)
    if details is not None:
        out["cached_tokens"] = getattr(details, "cached_tokens", None)
    return out


def explain_judgement(run_bundle: dict, judgement: dict | None = None,
                      context: str | None = None) -> dict:
    """调用 LLM 生成解释。

    Args:
        run_bundle: 运行数据包。
        judgement: 结构化判断（默认现场算）。
        context:   **可选** —— 直接指定发给 LLM 的上下文原文。
                   不传时用 format_judgement_context(run_bundle)（行为不变）。
                   S4-3 的 A/B 对照实验用它注入"证据组织方式不同、其它相同"
                   的两个上下文；不传这个参数时本函数行为与原来完全一致。

    Returns:
        {"answer": str, "model": str, "context": str, "content_length": int,
         "preview": str, "raw_reasoning_length": int, "usage": dict}
    """
    from B01.llm import get_client, describe_exception

    if judgement is None:
        from B07.judgement import build_judgement
        judgement = build_judgement(run_bundle)

    if context is None:
        context = format_judgement_context(run_bundle)

    try:
        resp = get_client().chat.completions.create(
            model=os.environ["LLM_MODEL"],
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": context + "\n\n【问题】\n" + FIXED_QUESTION},
            ],
        )
    except Exception as e:
        raise ExplainError(f"LLM 调用失败：{describe_exception(e)}") from e

    message = resp.choices[0].message
    content = (message.content or "").strip()
    reasoning = (getattr(message, "reasoning_content", "") or "").strip()

    # 调试细节：长度 + 前 50 字
    print(f"  [debug] content 长度 = {len(content)}")
    print(f"  [debug] content 前 {DEBUG_PREVIEW_CHARS} 字 = {content[:DEBUG_PREVIEW_CHARS]!r}")
    print(f"  [debug] reasoning_content 长度 = {len(reasoning)}")

    if len(content) == 0:
        if len(reasoning) > 0:
            raise ExplainError(
                f"LLM 返回的 message.content 为空，但 reasoning_content 有 {len(reasoning)} 字"
                f"（部分模型会把内容放进 reasoning_content）。"
                f"reasoning 前 {DEBUG_PREVIEW_CHARS} 字 = {reasoning[:DEBUG_PREVIEW_CHARS]!r}。"
                f"这是真实的空回答，视为调用失败。"
            )
        raise ExplainError(
            "LLM 返回的 message.content 为空，且 reasoning_content 也为空。"
            "这是真实的空回答，视为调用失败，不使用静态文案伪装成功。"
        )

    return {
        "answer": content,
        "model": resp.model,
        "context": context,
        "content_length": len(content),
        "preview": content[:DEBUG_PREVIEW_CHARS],
        "raw_reasoning_length": len(reasoning),
        "question": FIXED_QUESTION,
        # S4-3：token 计数（prompt_tokens / cached_tokens 用于唯一变量校验）
        "usage": _usage_of(resp),
        # 提示词指纹：S1 / S2 两次存档可以直接比对"是不是同一套提示词、同一个问题"
        "system_prompt_sha256": _sha12(SYSTEM_PROMPT),
        "question_sha256": _sha12(FIXED_QUESTION),
    }


# --------------------------------------------------------------------------
# 一致性校验（只记录，不修改）
# --------------------------------------------------------------------------

def _collect_numbers(obj, acc: set[int]) -> set[int]:
    """递归收集所有数值，四舍五入到整数。

    容差：浮点证据值同时按 ×100（百分比）和 ×1000（千分位）换算进集合，
    这样"证据里 recall=0.448、解释写 45%"属于正常换算，不报警。
    """
    if isinstance(obj, bool):
        return acc
    if isinstance(obj, (int, float)):
        v = float(obj)
        # 正负号都收：解释里写 "-12.1" 会被抽成正数 12，
        # 只存负数会把同一个数值误判成"事实越界"
        for x in (v, abs(v)):
            acc.add(int(round(x)))
            acc.add(int(round(x * 100)))
            acc.add(int(round(x * 1000)))
        return acc
    if isinstance(obj, dict):
        for v in obj.values():
            _collect_numbers(v, acc)
        return acc
    if isinstance(obj, (list, tuple)):
        for v in obj:
            _collect_numbers(v, acc)
        return acc
    return acc


def _extract_text_numbers(text: str) -> set[int]:
    """从 LLM 文本中提取数字（四舍五入到整数）。

    先剔除时间、日期、模块编号、版本号，避免把结构性数字当成"编造的数值"。
    """
    t = TIME_PATTERN.sub(" ", text)
    t = DATE_PATTERN.sub(" ", t)
    t = CN_DATE_PATTERN.sub(" ", t)
    t = CN_DATE_PART_PATTERN.sub(" ", t)
    # 顺序要求：先剔版本号，再剔模块编号。
    # 反过来的话，模块编号正则（IGNORECASE）会先吃掉版本号里的 "b06"，
    # 留下 "-20261020-v1"，版本号正则就再也匹配不上了。
    t = VERSION_TOKEN_PATTERN.sub(" ", t)
    t = MODULE_CODE_PATTERN.sub(" ", t)

    out: set[int] = set()
    for m in NUMBER_PATTERN.findall(t):
        try:
            out.add(int(round(float(m))))
        except ValueError:
            continue
    return out


def compare_judgement_vs_explanation(judgement: dict, explanation: dict, run_bundle: dict) -> list[str]:
    """一致性校验：只记录 warning，不修改结构化判断，也不修改解释。

    a. 结论冲突：结构化判断为 NORMAL，解释却要求立即行动
    b. 事实越界：解释里出现了证据集合中找不到的数字

    返回 warning 列表。
    """
    warnings: list[str] = []
    answer = (explanation.get("answer") or "") if isinstance(explanation, dict) else str(explanation)

    # ---- a. 结论冲突 ----
    predicted = judgement.get("predicted_attention")
    if predicted == "NORMAL":
        hits = [p for p in CONFLICT_PHRASES if p in answer]
        if hits:
            warnings.append(
                f"解释与结构化判断存在冲突：结构化判断 predicted_attention=NORMAL，"
                f"但解释中出现要求行动的表述 {hits}"
            )

    # ---- b. 事实越界（启发式检查，可能误报）----
    # 证据集合 = run_bundle 数值 ∪ judgement 数值（judgement 由 run_bundle 派生）
    evidence_numbers = _collect_numbers(run_bundle, set())
    evidence_numbers |= _collect_numbers(judgement, set())

    llm_numbers = _extract_text_numbers(answer)
    allow = ORDINAL_ALLOWLIST | STRUCTURAL_ALLOWLIST
    unknown = sorted(n for n in llm_numbers if n not in evidence_numbers and n not in allow)

    if unknown:
        warnings.append(
            f"解释引入了未取得的事实：数字 {unknown} 在证据集合中找不到"
            f"（启发式检查，可能误报）"
        )

    return warnings


# --------------------------------------------------------------------------
# 主程序
# --------------------------------------------------------------------------

def main():
    import argparse
    from datetime import datetime

    parser = argparse.ArgumentParser(description="B07 Step 1: LLM 解释")
    parser.add_argument("--anchor", type=str, default="2026-10-21T16:00:00")
    parser.add_argument("--location", type=str, default="A区-3号温室")
    parser.add_argument("--model", type=str,
                        default=str(_PROJECT_ROOT / "B06" / "artifacts" / "b06-20261020-v1"))
    args = parser.parse_args()

    print("=" * 70)
    print("B07 Step 1（第四步 B）— LLM 解释（独立环节）")
    print("=" * 70)

    # ---- 前置：evidence → predict → judgement ----
    from B07.evidence import build_evidence
    from B07.predict import predict_for_run
    from B07.judgement import build_judgement, self_check

    anchor = datetime.fromisoformat(args.anchor)
    print(f"\n[1/4] 构造 run_bundle（anchor={anchor.isoformat()}）...")
    evidence = build_evidence(anchor, args.location)
    run_bundle = predict_for_run(evidence, args.model)
    judgement = build_judgement(run_bundle)
    run_bundle["judgement"] = judgement
    check = self_check(run_bundle)
    print(f"  结构化判断字段: {check['fields']}")
    print(f"  复算自检: {'✅ 一致' if check['identical'] else '❌ 不一致: ' + str(check['differing_fields'])}")

    # ---- LLM 解释 ----
    print("\n[2/4] 调用 LLM 生成解释...")
    try:
        explanation = explain_judgement(run_bundle, judgement)
    except ExplainError as e:
        print(f"\n  ❌ {e}")
        sys.exit(1)

    # ---- 一致性校验 ----
    print("\n[3/4] 一致性校验（只记录，不修改）...")
    warnings = compare_judgement_vs_explanation(judgement, explanation, run_bundle)

    # ---- 输出 ----
    print("\n" + "=" * 70)
    print("【LLM 解释原文】")
    print("=" * 70)
    print(explanation["answer"])
    print("\n" + "-" * 70)
    print(f"  model:           {explanation['model']}")
    print(f"  content 长度:    {explanation['content_length']}")
    print(f"  reasoning 长度:  {explanation['raw_reasoning_length']}")
    print(f"  固定问题:        {explanation['question']}")

    print("\n" + "=" * 70)
    print("【一致性校验结果】")
    print("=" * 70)
    print(f"  a. 结论冲突: {'⚠️  发现冲突' if any('冲突' in w for w in warnings) else '✅ 无冲突'}")
    print(f"  b. 事实越界: {'⚠️  发现越界' if any('未取得的事实' in w for w in warnings) else '✅ 无越界'}")
    print(f"\n  warning 明细（{len(warnings)} 条）:")
    for w in warnings:
        print(f"    ⚠️  {w}")

    print("\n" + "=" * 70)
    print("B07 Step 1（第四步 B）完成 ✅")
    print("=" * 70)


if __name__ == "__main__":
    main()
