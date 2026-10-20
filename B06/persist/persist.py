"""B06 Step 5 — 模型保存与重载持久化

保存内容：
  - 训练好的 LogisticRegression 模型（joblib）
  - FEATURE_COLUMNS（特征名称与顺序）
  - WINDOW_SIZE / TARGET_SIZE（窗口配置）
  - TRAIN_DAYS / TEST_DAYS（数据切分时间边界）
  - label_source（标签规则版本）
  - model_version（带日期的可追溯命名）
  - 训练时间戳
  - 评估指标（Accuracy/Precision/Recall/F1）
  - 依赖库版本

一致性校验：
  - 加载时校验 FEATURE_COLUMNS
  - 特征名称/顺序对不上直接报错，不静默继续

CLI 用法（项目根目录执行）：
  保存：python -m B06.persist save --model artifacts/b06-YYYYMMDD-vN.joblib
  重载：python -m B06.persist load --model artifacts/b06-YYYYMMDD-vN.joblib

重载后自动用测试集预测，打印加载前后评估指标对比。
"""

import sys
import json
import joblib
from datetime import datetime, timezone
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

# --------------------------------------------------------------------------
# 内部依赖
# --------------------------------------------------------------------------

from B06.config import (
    FEATURE_COLUMNS,
    LABEL_SOURCE,
)
from B06 import SPEC as _spec
from B06 import config as _cfg


# --------------------------------------------------------------------------
# 保存
# --------------------------------------------------------------------------

def save_model(
    model,
    feature_names: list[str],
    metrics: dict,
    version: str,
    path: str | Path,
):
    """保存模型 + 元数据到 joblib + JSON 清单。"""
    import sklearn
    import numpy as np

    meta = {
        "model_version": version,
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "sklearn_version": sklearn.__version__,
        "numpy_version": np.__version__,
        "feature_columns": feature_names,          # 必须与训练时完全一致
        "feature_columns_count": len(feature_names),
        "window_size": _spec.WINDOW_SIZE,
        "target_size": _spec.TARGET_SIZE,
        "train_days": _cfg.TRAIN_DAYS,
        "test_days": _cfg.TEST_DAYS,
        "label_source": LABEL_SOURCE,
        "label_source_version": _cfg.RULE_VERSION,
        "metrics": {k: float(v) for k, v in metrics.items()},
        "model_type": "LogisticRegression",
        "model_class": "sklearn.linear_model.LogisticRegression",
    }

    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # joblib
    joblib_path = out_path.with_suffix(".joblib")
    joblib.dump(model, joblib_path)
    print(f"  模型保存: {joblib_path}")

    # JSON 清单
    json_path = out_path.with_suffix(".json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print(f"  清单保存: {json_path}")
    print(f"  version: {version}")

    return meta


# --------------------------------------------------------------------------
# 加载（一致性校验）
# --------------------------------------------------------------------------

def load_model(path: str | Path) -> tuple:
    """加载模型并校验一致性。

    Returns: (model, meta)

    一致性校验失败时直接抛出 ValueError，不静默继续。
    """
    import sklearn

    path = Path(path)

    # 读清单
    json_path = path.with_suffix(".json")
    if not json_path.exists():
        raise FileNotFoundError(f"清单文件不存在: {json_path}")

    with open(json_path, encoding="utf-8") as f:
        meta = json.load(f)

    # 加载模型
    joblib_path = path.with_suffix(".joblib")
    if not joblib_path.exists():
        raise FileNotFoundError(f"模型文件不存在: {joblib_path}")
    model = joblib.load(joblib_path)

    # ========== 一致性校验 ==========
    errors: list[str] = []

    # 1. 特征名称校验
    saved_features = meta.get("feature_columns", [])
    current_features = FEATURE_COLUMNS

    if saved_features != current_features:
        errors.append(
            f"FEATURE_COLUMNS 不一致:\n"
            f"  保存时: {saved_features}\n"
            f"  当前:   {current_features}"
        )

    # 2. 特征顺序校验
    if saved_features and saved_features != current_features:
        for i, (saved, current) in enumerate(zip(saved_features, current_features)):
            if saved != current:
                errors.append(f"  第 {i} 列顺序不一致: 保存='{saved}' 当前='{current}'")

    # 3. 窗口配置校验
    if meta.get("window_size") != _spec.WINDOW_SIZE:
        errors.append(
            f"WINDOW_SIZE 不一致: 保存={meta.get('window_size')} 当前={_spec.WINDOW_SIZE}"
        )
    if meta.get("target_size") != _spec.TARGET_SIZE:
        errors.append(
            f"TARGET_SIZE 不一致: 保存={meta.get('target_size')} 当前={_spec.TARGET_SIZE}"
        )

    # 4. 标签规则版本校验
    if meta.get("label_source") != LABEL_SOURCE:
        errors.append(
            f"label_source 不一致: 保存={meta.get('label_source')} 当前={LABEL_SOURCE}"
        )

    # 5. 模型类型校验
    model_type = meta.get("model_type", "")
    if "LogisticRegression" not in model_type:
        errors.append(f"模型类型不匹配: {model_type}")

    if errors:
        raise ValueError("\n".join(["一致性校验失败，模型无法加载:"] + errors))

    print("  ✅ 一致性校验通过")
    print(f"  model_version: {meta['model_version']}")
    print(f"  训练指标: Accuracy={meta['metrics']['accuracy']:.3f} "
          f"Precision={meta['metrics']['precision']:.3f} "
          f"Recall={meta['metrics']['recall']:.3f} "
          f"F1={meta['metrics']['f1']:.3f}")

    return model, meta


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _get_model_and_metrics():
    """训练模型并返回 model + 原始 metrics（供 save 用）。"""
    # 复用 train.py 的加载逻辑
    from B06.generate import load_samples, generate_windows
    from B06.feature import extract_features
    from B06.split import temporal_split
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import accuracy_score, precision_score, recall_score, f1_score

    samples_b02 = load_samples()
    windows = generate_windows(
        samples_b02,
        window_size=_spec.WINDOW_SIZE,
        target_size=_spec.TARGET_SIZE,
    )
    if _spec.DTW_COMPUTE and windows:
        from B06.SPEC import compute_dtw
        for i in range(len(windows)):
            hw = windows[:i]
            if hw:
                windows[i] = compute_dtw(windows[i], hw[-_spec.DTW_WINDOW_LIMIT:])

    all_s = []
    for w in windows:
        feats = extract_features(w)
        from B06.config import TEMP_MIN, TEMP_MAX, HUM_MIN, HUM_MAX, LUX_MAX
        window_end = w.history[-1].timestamp
        label_window_start = w.target[0].timestamp
        target_temps = [s.temperature for s in w.target]
        target_hums = [s.humidity for s in w.target]
        target_luxs = [s.lux for s in w.target]
        reasons = []
        for i, t in enumerate(target_temps):
            if t < TEMP_MIN: reasons.append(f"TEMP#{i+1}={t:.1f}<{TEMP_MIN}")
            elif t > TEMP_MAX: reasons.append(f"TEMP#{i+1}={t:.1f}>{TEMP_MAX}")
        for i, h in enumerate(target_hums):
            if h < HUM_MIN: reasons.append(f"HUM#{i+1}={h:.1f}<{HUM_MIN}")
            elif h > HUM_MAX: reasons.append(f"HUM#{i+1}={h:.1f}>{HUM_MAX}")
        for i, l in enumerate(target_luxs):
            if l > LUX_MAX: reasons.append(f"LUX#{i+1}={l:.0f}>{LUX_MAX}")
        label = "WATCH" if reasons else "NORMAL"
        reason = " 触发 ".join(reasons) if reasons else "全部指标在正常范围内"

        class _S:
            pass
        s = _S()
        s.window_id = w.window_id
        s.window_end = window_end
        s.label_window_start = label_window_start
        s.features = feats
        s.label = label
        s.label_reason = reason
        all_s.append(s)

    train, test, dropped = temporal_split(all_s)
    # 复用 config.py 中定义的特征顺序，不重新排序
    from B06.config import FEATURE_COLUMNS as FEATURE_ORDER
    feature_names = FEATURE_ORDER
    X_train = [[s.features.get(fn, 0) for fn in feature_names] for s in train]
    X_test = [[s.features.get(fn, 0) for fn in feature_names] for s in test]
    y_train = [s.label for s in train]
    y_test = [s.label for s in test]

    model = LogisticRegression(random_state=42, max_iter=1000)
    model.fit(X_train, y_train)
    y_pred = model.predict(X_test)

    metrics = {
        "accuracy": accuracy_score(y_test, y_pred),
        "precision": precision_score(y_test, y_pred, pos_label="WATCH", zero_division=0),
        "recall": recall_score(y_test, y_pred, pos_label="WATCH", zero_division=0),
        "f1": f1_score(y_test, y_pred, pos_label="WATCH", zero_division=0),
    }

    return model, feature_names, metrics, test, y_test, y_pred


def cmd_save(model_path: str):
    """CLI save 命令。"""
    print("=" * 60)
    print("B06 Step 5 — 保存模型")
    print("=" * 60)

    # 生成 version
    ts = datetime.now(timezone.utc).strftime("%Y%m%d")
    base = Path(model_path).stem  # e.g. "b06-20261020-v1"
    version = base or f"b06-{ts}-v1"

    print(f"\n训练模型（复用 train.py 逻辑）...")
    model, feature_names, metrics, test, y_test, y_pred = _get_model_and_metrics()
    print(f"  模型: LogisticRegression")
    print(f"  特征数: {len(feature_names)}")

    print(f"\n保存到: {model_path}")
    meta = save_model(model, feature_names, metrics, version, model_path)
    print(f"\n保存完成 ✅")


def cmd_load(model_path: str):
    """CLI load 命令：加载 + 自动用测试集预测 + 指标对比。"""
    print("=" * 60)
    print("B06 Step 5 — 重载模型并验证")
    print("=" * 60)

    print(f"\n加载模型: {model_path}")
    model, meta = load_model(model_path)

    # 用测试集预测
    print("\n用测试集预测（加载后）...")
    _, _, _, test, y_test, y_pred_before = _get_model_and_metrics()

    # 重新从模型预测（与上面的 y_pred 应该一致）
    feature_names = meta["feature_columns"]
    X_test = [[s.features.get(fn, 0) for fn in feature_names] for s in test]
    y_pred_loaded = model.predict(X_test)

    from sklearn.metrics import accuracy_score, precision_score, recall_score, f1_score
    loaded_metrics = {
        "accuracy": accuracy_score(y_test, y_pred_loaded),
        "precision": precision_score(y_test, y_pred_loaded, pos_label="WATCH", zero_division=0),
        "recall": recall_score(y_test, y_pred_loaded, pos_label="WATCH", zero_division=0),
        "f1": f1_score(y_test, y_pred_loaded, pos_label="WATCH", zero_division=0),
    }
    saved_metrics = meta["metrics"]

    # 指标对比
    print()
    print("=" * 60)
    print("【加载前后评估指标对比】")
    print("=" * 60)
    print(f"  {'指标':<12}  {'保存时':>10}  {'加载后':>10}  {'一致':>6}")
    print(f"  {'-'*12}  {'-'*10}  {'-'*10}  {'-'*6}")
    all_match = True
    for key in ["accuracy", "precision", "recall", "f1"]:
        saved_v = saved_metrics.get(key, 0)
        loaded_v = loaded_metrics.get(key, 0)
        match = abs(saved_v - loaded_v) < 1e-9
        if not match:
            all_match = False
        print(f"  {key.capitalize():<12}  {saved_v:>10.4f}  {loaded_v:>10.4f}  {'✅' if match else '❌'}")

    print()
    # 预测结果逐条一致性
    predictions_match = (y_pred_before == y_pred_loaded)
    if all_match and bool(predictions_match.all()):
        print("  ✅ 加载后预测结果与保存时完全一致")
    else:
        print("  ❌ 警告：预测结果存在差异，请检查模型是否被意外修改")

    print()
    print(f"  保存时间: {meta.get('saved_at', 'unknown')}")
    print(f"  当前时间: {datetime.now(timezone.utc).isoformat()}")
    print(f"  模型版本: {meta.get('model_version', 'unknown')}")
    print(f"  标签规则: {meta.get('label_source', 'unknown')} / {meta.get('label_source_version', 'unknown')}")

    print()
    print("=" * 60)
    print("B06 Step 5 验证完成 ✅")
    print("=" * 60)


# --------------------------------------------------------------------------
# CLI 入口
# --------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="B06 Step 5: 模型保存与重载")
    sub = parser.add_subparsers(dest="cmd", required=True)

    save_p = sub.add_parser("save", help="保存模型")
    save_p.add_argument("--model", required=True, help="输出路径，如 artifacts/b06-20261020-v1")

    load_p = sub.add_parser("load", help="加载模型并验证")
    load_p.add_argument("--model", required=True, help="模型路径，如 artifacts/b06-20261020-v1")

    args = parser.parse_args()

    if args.cmd == "save":
        cmd_save(args.model)
    elif args.cmd == "load":
        cmd_load(args.model)
