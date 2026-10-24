"""B07 Step 1（第三步）— 加载 B06 已保存模型，用最新窗口做一次预测

绝对约束：
  - 只加载已有产物，不重新训练、不调参
  - 不调用 B06.persist.save（该命令内部会重新 fit）
  - 不改 B06 / B02 / B05 任何文件
  - 不重新切分数据
  - target 必须是空列表（未来点还没发生，不能抄答案）
  - 不接 B03 天气、不接 B04 知识库
  - 预测结果是证据，不是命令

解释方法说明：
  shap 未安装，B06 的 shap.top5_contributions 走降级路径
  coef*(X - X.mean(axis=0))，对单行输入恒等于 0，没有信息量。
  本步改用 model.coef_[0] 排序展示特征影响方向，
  并在字段中标注 "feature_contribution_method": "logistic_coefficient_ranking"。

用法：
  python B07/predict.py --anchor 2026-10-21T16:00:00
  python B07/predict.py --anchor 2026-10-21T16:00:00 --json
"""

import sys
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from B07.config import DEFAULT_LOCATION, HISTORY_POINTS

DEFAULT_MODEL_PATH = _PROJECT_ROOT / "B06" / "artifacts" / "b06-20261020-v1"


class PredictValidationError(ValueError):
    """模型产物 meta 校验失败。直接中止，不静默继续。"""


# --------------------------------------------------------------------------
# meta 校验
# --------------------------------------------------------------------------

def validate_meta(meta: dict) -> dict:
    """校验已保存模型的 meta，不通过直接抛错中止。

    Returns: 校验摘要（model_version / label_source / feature_count 等）
    """
    from B06.config import FEATURE_COLUMNS
    from B06 import SPEC as spec

    errors: list[str] = []

    # 1. model_version 非空
    model_version = meta.get("model_version")
    if not model_version or not str(model_version).strip():
        errors.append(f"model_version 缺失或为空（meta 中 = {model_version!r}）")

    # 2. feature_columns 与 B06.config.FEATURE_COLUMNS 完全一致（33 列 + 顺序）
    saved_features = meta.get("feature_columns")
    if saved_features is None:
        errors.append("feature_columns 缺失")
    elif list(saved_features) != list(FEATURE_COLUMNS):
        diff_detail = []
        if len(saved_features) != len(FEATURE_COLUMNS):
            diff_detail.append(f"列数不一致：保存 {len(saved_features)} vs 期望 {len(FEATURE_COLUMNS)}")
        for i, (a, b) in enumerate(zip(saved_features, FEATURE_COLUMNS)):
            if a != b:
                diff_detail.append(f"第 {i} 列：保存='{a}' 期望='{b}'")
        errors.append("feature_columns 与 B06.config.FEATURE_COLUMNS 不一致：\n    "
                      + "\n    ".join(diff_detail))

    # 3. window_size / target_size
    if meta.get("window_size") != spec.WINDOW_SIZE:
        errors.append(
            f"window_size 不一致：保存={meta.get('window_size')} 期望={spec.WINDOW_SIZE}"
        )
    if meta.get("target_size") != spec.TARGET_SIZE:
        errors.append(
            f"target_size 不一致：保存={meta.get('target_size')} 期望={spec.TARGET_SIZE}"
        )

    if errors:
        raise PredictValidationError(
            "模型产物 meta 校验失败，本次运行中止：\n  - " + "\n  - ".join(errors)
        )

    return {
        "model_version": model_version,
        "label_source": meta.get("label_source"),
        "label_source_version": meta.get("label_source_version"),
        "feature_count": len(saved_features),
        "window_size": meta.get("window_size"),
        "target_size": meta.get("target_size"),
        "saved_at": meta.get("saved_at"),
        "sklearn_version": meta.get("sklearn_version"),
        "metrics": meta.get("metrics") or {},
    }


# --------------------------------------------------------------------------
# evidence → B06 Sample
# --------------------------------------------------------------------------

def evidence_history_to_samples(evidence: dict) -> list:
    """把 evidence["history"]（dict 列表）转回 B06.SPEC.Sample 列表。

    source_type 原样保留（小写 'simulated'），不做改写。
    """
    from B06.SPEC import Sample

    history = evidence.get("history") or []
    if len(history) != HISTORY_POINTS:
        raise PredictValidationError(
            f"evidence.history 点数异常：期望 {HISTORY_POINTS}，实际 {len(history)}"
        )

    samples = []
    for i, h in enumerate(history):
        st = h.get("source_type")
        if st is None or st == "":
            raise PredictValidationError(f"evidence.history 第 {i+1} 点 source_type 缺失")
        samples.append(Sample(
            timestamp=datetime.fromisoformat(h["timestamp"]),
            temperature=h["temperature"],
            humidity=h["humidity"],
            lux=h["lux"],
            source_type=st,
            sim_scenario=h.get("sim_scenario", ""),
        ))
    return samples


# --------------------------------------------------------------------------
# 系数排序（替代 SHAP）
# --------------------------------------------------------------------------

def rank_by_coefficient(model, top_k: int = 5) -> list[dict]:
    """按 model.coef_[0] 排序输出 Top-k 特征影响方向。

    sklearn 二分类：coef_ 对应 classes_[1]，
    系数为正 → 推向 classes_[1]，为负 → 推向 classes_[0]。
    """
    from B06.config import FEATURE_COLUMNS

    coef = list(model.coef_[0])
    classes = list(model.classes_)
    push_class = classes[1] if len(classes) > 1 else classes[0]
    pull_class = classes[0]

    paired = sorted(zip(FEATURE_COLUMNS, coef), key=lambda x: abs(x[1]), reverse=True)

    result = []
    for name, value in paired[:top_k]:
        if value > 0:
            direction = f"推向{push_class}"
        elif value < 0:
            direction = f"推向{pull_class}"
        else:
            direction = "无方向（系数为 0）"
        result.append({
            "feature": name,
            "coefficient": round(float(value), 6),
            "direction": direction,
        })
    return result


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------

def predict_for_run(evidence: dict, model_path: Path | str = DEFAULT_MODEL_PATH) -> dict:
    """用 S1-2 的 evidence 做一次预测，返回合并后的完整运行对象。

    Returns:
        {**evidence, "model_output": {...}, "warnings": [...]}
    """
    warnings: list[str] = []

    # ---------------- 1. 载入并校验 meta ----------------
    import json
    from B06.inference.inference import load_for_inference, predict_window, _build_history_windows

    model_path = Path(model_path)

    # 1a. 先读 manifest 自行校验（不加载模型），不通过直接中止
    manifest_path = model_path.with_suffix(".json")
    if not manifest_path.exists():
        raise PredictValidationError(f"模型清单不存在：{manifest_path}")
    try:
        with open(manifest_path, encoding="utf-8") as f:
            manifest = json.load(f)
    except json.JSONDecodeError as e:
        raise PredictValidationError(f"模型清单解析失败：{manifest_path} — {e}") from e

    meta_info = validate_meta(manifest)

    # 1b. 再加载模型（B06 内部会再做一次一致性校验，作为第二道防线）
    try:
        model, meta = load_for_inference(str(model_path))
    except (ValueError, FileNotFoundError) as e:
        raise PredictValidationError(
            f"模型加载被 B06 一致性校验拦截，本次运行中止：\n  {e}"
        ) from e

    # ---------------- 2. 构造实时窗口 ----------------
    from B06.SPEC import TimeWindow

    hist_samples = evidence_history_to_samples(evidence)

    window = TimeWindow(
        window_id="s1-live-001",
        source="SIMULATED",       # B06 的 SourceType 枚举是大写
        history=hist_samples,
        target=[],                # 必须为空：未来点还没发生
    )

    # ---------------- 3. 推理 ----------------
    history_windows = _build_history_windows(limit=50)
    out = predict_window(window, model, meta, history_windows=history_windows)

    # ---------------- 4. 组装 model_output ----------------
    model_output = {
        "evidence_type": "MODEL_OUTPUT",
        "window_id": out.window_id,
        "predicted_attention": out.predicted_attention,
        "confidence": out.confidence,
        "confidence_normal": out.confidence_normal,
        "confidence_watch": out.confidence_watch,
        "model_version": out.model_version,
        "dtw_temperature": out.dtw_temperature,
        "dtw_humidity": out.dtw_humidity,
        "dtw_lux": out.dtw_lux,
        "feature_count": out.feature_count,
        "feature_contribution_method": "logistic_coefficient_ranking",
        "feature_contributions_top5": rank_by_coefficient(model, top_k=5),
        "errors": list(out.errors),
    }

    # ---------------- 5. 合理性体检（只记 warning，不改结果） ----------------
    # 5.1 errors 非空
    if out.errors:
        warnings.append(
            f"推理过程产生 {len(out.errors)} 项异常，已原样保留在 model_output.errors："
            + "；".join(out.errors)
        )

    # 5.2 模型输出 WATCH 但稳定基线判定为"稳定" → 矛盾告警
    stability_verdict = None
    try:
        from B07.stability import judge_stability
        judged = judge_stability(hist_samples)
        stability_verdict = "稳定" if judged["passed"] else "不稳定"
        if out.predicted_attention == "WATCH" and judged["passed"]:
            warnings.append(
                "模型输出 WATCH 与稳定基线矛盾，建议回 B06 检查模型或场景数据"
            )
    except Exception as e:
        warnings.append(f"稳定基线复核失败（不影响预测结果）：{type(e).__name__}: {e}")

    model_output["stability_baseline"] = stability_verdict

    # 5.3 hours_gap 提示（沿用 evidence 的 warning）
    for w in evidence.get("warnings", []) or []:
        warnings.append(w)

    return {**evidence, "model_output": model_output, "model_meta": meta_info, "warnings": warnings}


# --------------------------------------------------------------------------
# 主程序
# --------------------------------------------------------------------------

def main():
    import argparse
    import json

    parser = argparse.ArgumentParser(description="B07 Step 1: 模型预测")
    parser.add_argument("--anchor", type=str, default="2026-10-21T16:00:00",
                        help="时间锚点，ISO 格式")
    parser.add_argument("--location", type=str, default=DEFAULT_LOCATION)
    parser.add_argument("--model", type=str, default=str(DEFAULT_MODEL_PATH))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    print("=" * 70)
    print("B07 Step 1（第三步）— 加载 B06 模型做一次预测")
    print("=" * 70)

    # ---- Step 2: 构造 evidence ----
    from B07.evidence import build_evidence
    anchor = datetime.fromisoformat(args.anchor)
    print(f"\n[1/3] 构造 evidence（anchor={anchor.isoformat()}）...")
    evidence = build_evidence(anchor, args.location)
    print(f"  历史点数: {evidence['history_window']['count']}")
    print(f"  current_state: {evidence['current_state']['temperature']}℃ / "
          f"{evidence['current_state']['humidity']}% / {evidence['current_state']['lux']} lux")
    print(f"  hours_gap: {evidence['hours_gap']}")

    # ---- Step 3: 预测 ----
    print(f"\n[2/3] 加载模型并预测...")
    print(f"  加载路径: {args.model}")
    try:
        run = predict_for_run(evidence, args.model)
    except PredictValidationError as e:
        print(f"\n  ❌ {e}")
        sys.exit(1)

    mm = run["model_meta"]
    print("  ── meta 校验（通过才继续）──")
    print(f"  model_version:      {mm['model_version']}")
    print(f"  label_source:       {mm['label_source']} / {mm['label_source_version']}")
    print(f"  feature_count:      {mm['feature_count']}（与 B06.config.FEATURE_COLUMNS 顺序一致）")
    print(f"  window_size/target: {mm['window_size']} / {mm['target_size']}")
    print(f"  saved_at:           {mm['saved_at']}")
    print(f"  sklearn_version:    {mm['sklearn_version']}")

    mo = run["model_output"]

    # ---- Step 4: 输出 ----
    print(f"\n[3/3] 结果")
    print("=" * 70)
    print("【MODEL_OUTPUT】")
    print("=" * 70)
    print(f"  evidence_type:        {mo['evidence_type']}")
    print(f"  window_id:            {mo['window_id']}")
    print(f"  predicted_attention:  {mo['predicted_attention']}")
    print(f"  confidence:           {mo['confidence']:.4f}")
    print(f"  confidence_normal:    {mo['confidence_normal']:.4f}")
    print(f"  confidence_watch:     {mo['confidence_watch']:.4f}")
    print(f"  model_version:        {mo['model_version']}")
    print(f"  dtw_temperature:      {mo['dtw_temperature']}")
    print(f"  dtw_humidity:         {mo['dtw_humidity']}")
    print(f"  dtw_lux:              {mo['dtw_lux']}")
    print(f"  feature_count:        {mo['feature_count']}")
    print(f"  stability_baseline:   {mo['stability_baseline']}")
    print(f"  errors:               {mo['errors']}")

    print(f"\n  【特征影响方向 Top5】方法：{mo['feature_contribution_method']}")
    print(f"  （注意：这是系数排序，非 SHAP；shap 未安装，B06 的 shap.top5 恒为 0）")
    for i, c in enumerate(mo["feature_contributions_top5"], 1):
        print(f"    {i}. {c['feature']:<26} {c['coefficient']:>+9.4f}  {c['direction']}")

    print("\n" + "=" * 70)
    print("【warnings】")
    print("=" * 70)
    if run["warnings"]:
        for w in run["warnings"]:
            print(f"  ⚠️  {w}")
    else:
        print("  无")

    # ---- 完成标准：三项存在性检查 ----
    print("\n" + "=" * 70)
    print("【本次运行完成标准：三项存在性检查】")
    print("=" * 70)
    has_current_state = bool(run.get("current_state"))
    has_history = (len(run.get("history") or []) == HISTORY_POINTS)
    has_model_output = bool(run.get("model_output"))
    print(f"  has_current_state:  {has_current_state}   （当前状态）")
    print(f"  has_history(6):     {has_history}   （历史数据，{len(run.get('history') or [])} 点）")
    print(f"  has_model_output:   {has_model_output}   （短时预测）")

    if args.json:
        print("\n" + "-" * 70)
        print(json.dumps(run, ensure_ascii=False, indent=2))

    all_ok = has_current_state and has_history and has_model_output
    print("\n" + "=" * 70)
    print(f"B07 Step 1（第三步）完成 {'✅' if all_ok else '❌'}")
    print("=" * 70)
    if not all_ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
