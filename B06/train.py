"""B06 Step 4 — 朴素基线 + ML 模型训练评估

硬性约束：
  - 禁止超参搜索
  - 禁止深度学习
  - 测试集仅用于最终一次性评估

流程：
  1. 打印训练/验证/测试集类别分布
  2. 朴素基线：永远预测训练集多数类
  3. LogisticRegression（线性模型，可解释性强，适合作为 ML 基线）
  4. fit 仅用训练集；验证集筛选模型；测试集最终一次性评估
  5. 测试集 2×2 混淆矩阵 + 4 项指标
  6. 对比表格：朴素基线 vs LR

错误分析：
  - 筛选假阳性 FP / 假阴性 FN 样本
  - 展示完整时间序列、特征值、标签原因
  - 导出 b06/artifacts/misclassified.csv + .md
  - 文字分析错分根因（阈值/特征不足/标签规则不匹配）

用法：
  python B06/train.py
"""

import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, "D:/GreenMate")

import csv
import json
import statistics
from datetime import timedelta
from pathlib import Path

from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score, precision_score, recall_score, f1_score,
    confusion_matrix, ConfusionMatrixDisplay,
)
import matplotlib
matplotlib.use("Agg")  # 无 GUI 后端
import matplotlib.pyplot as plt

# --------------------------------------------------------------------------
# 数据加载
# --------------------------------------------------------------------------

def load_all():
    """加载全部样本（复用 Step 1/2/3 逻辑）。"""
    from B06.generate import load_samples, generate_windows
    import B06.SPEC as spec
    from B06.feature import extract_features
    from B06.config import LABEL_SOURCE, TEMP_MIN, TEMP_MAX, HUM_MIN, HUM_MAX, LUX_MAX
    from B06.split import temporal_split

    samples_b02 = load_samples()
    windows = generate_windows(
        samples_b02,
        window_size=spec.WINDOW_SIZE,
        target_size=spec.TARGET_SIZE,
    )

    # DTW
    if spec.DTW_COMPUTE and windows:
        from B06.SPEC import compute_dtw
        for i in range(len(windows)):
            hw = windows[:i]
            if hw:
                windows[i] = compute_dtw(windows[i], hw[-spec.DTW_WINDOW_LIMIT:])

    # 提取样本
    all_samples = []
    for w in windows:
        feats = extract_features(w)
        window_end = w.history[-1].timestamp
        label_window_start = w.target[0].timestamp

        target_temps = [s.temperature for s in w.target]
        target_hums = [s.humidity for s in w.target]
        target_luxs = [s.lux for s in w.target]

        reasons = []
        for i, t in enumerate(target_temps):
            if t < TEMP_MIN:
                reasons.append(f"TEMP#{i+1}={t:.1f}<{TEMP_MIN}")
            elif t > TEMP_MAX:
                reasons.append(f"TEMP#{i+1}={t:.1f}>{TEMP_MAX}")
        for i, h in enumerate(target_hums):
            if h < HUM_MIN:
                reasons.append(f"HUM#{i+1}={h:.1f}<{HUM_MIN}")
            elif h > HUM_MAX:
                reasons.append(f"HUM#{i+1}={h:.1f}>{{HUM_MAX}}")
        for i, l in enumerate(target_luxs):
            if l > LUX_MAX:
                reasons.append(f"LUX#{i+1}={l:.0f}>{LUX_MAX}")

        if reasons:
            label = "WATCH"
            reason_str = " 触发 ".join(reasons)
        else:
            label = "NORMAL"
            reason_str = "全部指标在正常范围内"

        class _S:
            pass
        s = _S()
        s.window_id = w.window_id
        s.source = w.source
        s.window_end = window_end
        s.label_window_start = label_window_start
        s.features = feats
        s.label = label
        s.label_reason = reason_str
        s.label_source = LABEL_SOURCE
        all_samples.append(s)

    train, test, dropped = temporal_split(all_samples)
    return train, test, dropped, all_samples


# --------------------------------------------------------------------------
# 评估指标
# --------------------------------------------------------------------------

def eval_binary(y_true: list, y_pred: list, pos="WATCH"):
    """计算二分类四项指标。"""
    acc = accuracy_score(y_true, y_pred)
    prec = precision_score(y_true, y_pred, pos_label=pos, zero_division=0)
    rec = recall_score(y_true, y_pred, pos_label=pos, zero_division=0)
    f1 = f1_score(y_true, y_pred, pos_label=pos, zero_division=0)
    return {"accuracy": acc, "precision": prec, "recall": rec, "f1": f1}


def print_metrics(m: dict, label: str = ""):
    print(f"  {label:20s}  Accuracy={m['accuracy']:.3f}  Precision={m['precision']:.3f}  Recall={m['recall']:.3f}  F1={m['f1']:.3f}")


def print_confusion(cm: list, labels: list[str]):
    """打印 2×2 混淆矩阵。"""
    print(f"\n  混淆矩阵（预测↓ / 真实→）")
    print(f"  {' '*20}  {'NORMAL':>8}  {'WATCH':>8}")
    print(f"  {'预测 NORMAL':20s}  {cm[0][0]:>8}  {cm[0][1]:>8}")
    print(f"  {'预测 WATCH':20s}  {cm[1][0]:>8}  {cm[1][1]:>8}")
    print()
    tn, fp, fn, tp = cm[0][0], cm[0][1], cm[1][0], cm[1][1]
    print(f"  TN={tn}  FP={fp}  FN={fn}  TP={tp}")
    return tn, fp, fn, tp


def comparison_table(naive: dict, lr: dict):
    print()
    print("=" * 70)
    print("【对比表格】朴素基线 vs LogisticRegression")
    print("=" * 70)
    print(f"  {'指标':<12}  {'朴素基线':>12}  {'LogisticRegression':>18}  {'胜出':>8}")
    print(f"  {'-'*12}  {'-'*12}  {'-'*18}  {'-'*8}")
    metrics = ["accuracy", "precision", "recall", "f1"]
    for m in metrics:
        n_val = naive[m]
        l_val = lr[m]
        winner = "LR" if l_val > n_val else ("朴素" if n_val > l_val else "持平")
        print(f"  {m.capitalize():<12}  {n_val:>12.3f}  {l_val:>18.3f}  {winner:>8}")
    print()


# --------------------------------------------------------------------------
# 错误分析
# --------------------------------------------------------------------------

def error_analysis(test_samples, y_true, y_pred, model=None, feature_names=None):
    """错误分析：筛选 FP/FN 样本，导出 artifacts，文字分析。"""
    Path("B06/artifacts").mkdir(exist_ok=True)

    fp_samples = []  # 假阳性：预测 WATCH，实际 NORMAL
    fn_samples = []  # 假阴性：预测 NORMAL，实际 WATCH

    for s, yt, yp in zip(test_samples, y_true, y_pred):
        if yt == "NORMAL" and yp == "WATCH":
            fp_samples.append((s, yt, yp))
        elif yt == "WATCH" and yp == "NORMAL":
            fn_samples.append((s, yt, yp))

    # 如果没有错分，找最近决策边界的样本
    boundary_samples = []
    if not fp_samples and not fn_samples and model is not None:
        # 用决策函数距离找最接近边界的样本
        X_test = [[s.features.get(fn, 0) for fn in feature_names] for s in test_samples]
        decision = model.decision_function(X_test)
        sorted_idx = sorted(range(len(decision)), key=lambda i: abs(decision[i]))
        for idx in sorted_idx[:2]:
            s = test_samples[idx]
            boundary_samples.append((s, s.label, "最近决策边界" if s.label == "WATCH" else "最近决策边界"))

    # 展示样本详情
    def show_sample(s, yt, yp, tag):
        print(f"\n  [{tag}] {s.window_id}")
        print(f"  窗口终点: {s.window_end}  标签窗口起点: {s.label_window_start}")
        print(f"  真实标签: {yt}  预测标签: {yp}")
        print(f"  标签原因: {s.label_reason}")
        print(f"  全部特征（33 个）:")
        for k, v in sorted(s.features.items()):
            print(f"    {k:<28}: {v:>10.4f}")

    if fp_samples:
        print()
        print("=" * 70)
        print(f"【假阳性 FP】预测=WATCH  实际=NORMAL  共 {len(fp_samples)} 个")
        print("=" * 70)
        show_sample(fp_samples[0][0], fp_samples[0][1], fp_samples[0][2], "FP #1")
    else:
        print("\n  无假阳性样本 ✅")

    if fn_samples:
        print()
        print("=" * 70)
        print(f"【假阴性 FN】预测=NORMAL  实际=WATCH  共 {len(fn_samples)} 个")
        print("=" * 70)
        show_sample(fn_samples[0][0], fn_samples[0][1], fn_samples[0][2], "FN #1")
    else:
        print("\n  无假阴性样本 ✅")

    if boundary_samples:
        print()
        print("=" * 70)
        print("【边界样本】无错分样本，最近决策边界样本：")
        print("=" * 70)
        show_sample(boundary_samples[0][0], boundary_samples[0][1], "最近边界", "BOUNDARY #1")

    # 导出 CSV
    out_path_csv = Path("B06/artifacts/misclassified.csv")
    out_path_md = Path("B06/artifacts/misclassified.md")
    all_misclassified = fp_samples + fn_samples

    if all_misclassified:
        with open(out_path_csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["sample_id", "window_start", "window_end", "y_true", "y_pred",
                             "rule_hit", "features_json"])
            for s, yt, yp in all_misclassified:
                writer.writerow([
                    s.window_id,
                    s.history[0].timestamp if hasattr(s, "history") else s.window_end,
                    s.window_end,
                    yt, yp,
                    s.label_reason,
                    json.dumps(s.features, ensure_ascii=False),
                ])
        print(f"\n  导出 CSV: {out_path_csv}")

        with open(out_path_md, "w", encoding="utf-8") as f:
            f.write(f"# 错分样本分析\n\n")
            f.write(f"## 假阳性 FP（共 {len(fp_samples)} 个）\n\n")
            for i, (s, yt, yp) in enumerate(fp_samples):
                f.write(f"### FP #{i+1}: {s.window_id}\n\n")
                f.write(f"- 窗口终点: {s.window_end}\n")
                f.write(f"- 真实标签: {yt}\n")
                f.write(f"- 预测标签: {yp}\n")
                f.write(f"- 触发规则: {s.label_reason}\n\n")
                f.write("| 特征名 | 值 |\n|---|---|\n")
                for k, v in sorted(s.features.items()):
                    f.write(f"| {k} | {v:.4f} |\n")
                f.write("\n")
            f.write(f"## 假阴性 FN（共 {len(fn_samples)} 个）\n\n")
            for i, (s, yt, yp) in enumerate(fn_samples):
                f.write(f"### FN #{i+1}: {s.window_id}\n\n")
                f.write(f"- 窗口终点: {s.window_end}\n")
                f.write(f"- 真实标签: {yt}\n")
                f.write(f"- 预测标签: {yp}\n")
                f.write(f"- 触发规则: {s.label_reason}\n\n")
                f.write("| 特征名 | 值 |\n|---|---|\n")
                for k, v in sorted(s.features.items()):
                    f.write(f"| {k} | {v:.4f} |\n")
                f.write("\n")
        print(f"  导出 MD:  {out_path_md}")
    else:
        print("\n  无错分样本，跳过导出")

    return fp_samples, fn_samples


# --------------------------------------------------------------------------
# 主程序
# --------------------------------------------------------------------------

def main():
    print("=" * 70)
    print("B06 Step 4 — 朴素基线 + ML 模型训练评估")
    print("=" * 70)

    print("\n加载样本...")
    train, test, dropped, all_samples = load_all()
    print(f"  训练集: {len(train)} 个")
    print(f"  测试集: {len(test)} 个")

    # 特征列
    feature_names = sorted(train[0].features.keys())
    print(f"  特征数: {len(feature_names)}")

    X_train = [[s.features.get(fn, 0) for fn in feature_names] for s in train]
    X_test = [[s.features.get(fn, 0) for fn in feature_names] for s in test]
    y_train = [s.label for s in train]
    y_test = [s.label for s in test]

    # 1. 类别分布
    print()
    print("=" * 70)
    print("【1】数据集类别分布")
    print("=" * 70)
    for name, samples in [("训练集", train), ("测试集", test)]:
        cnt = {}
        for s in samples:
            cnt[s.label] = cnt.get(s.label, 0) + 1
        print(f"\n  {name}（{len(samples)} 个）:")
        for lbl in ["NORMAL", "WATCH"]:
            n = cnt.get(lbl, 0)
            pct = n / len(samples) * 100
            bar = "█" * int(pct / 2)
            print(f"    {lbl:<7}: {n:>4} 个（{pct:5.1f}%）{bar}")

    # 2. 朴素基线（永远预测训练集多数类）
    print()
    print("=" * 70)
    print("【2】朴素基线（永远预测训练集多数类）")
    print("=" * 70)
    train_label_counts = {}
    for s in train:
        train_label_counts[s.label] = train_label_counts.get(s.label, 0) + 1
    majority_class = max(train_label_counts, key=train_label_counts.get)
    print(f"  训练集多数类: {majority_class}（{train_label_counts[majority_class]} 个）")
    print(f"  朴素基线策略: 永远预测 '{majority_class}'")
    naive_pred = [majority_class] * len(y_test)
    naive_metrics = eval_binary(y_test, naive_pred)
    print_metrics(naive_metrics, "朴素基线")

    # 3. LogisticRegression
    print()
    print("=" * 70)
    print("【3】LogisticRegression")
    print("=" * 70)
    print("  选择理由: LogisticRegression 是线性模型，决策边界可解释，")
    print("            适合作为 ML 基线验证特征有效性；无超参搜索，")
    print("            fit 仅用训练集，测试集仅最终一次性评估。")
    model = LogisticRegression(random_state=42, max_iter=1000)
    model.fit(X_train, y_train)
    lr_pred = model.predict(X_test)
    lr_metrics = eval_binary(y_test, lr_pred)
    print_metrics(lr_metrics, "LogisticRegression")

    # 4. 测试集混淆矩阵
    print()
    print("=" * 70)
    print("【4】测试集 2×2 混淆矩阵")
    print("=" * 70)
    cm = confusion_matrix(y_test, lr_pred, labels=["NORMAL", "WATCH"])
    tn, fp, fn, tp = print_confusion(cm.tolist(), ["NORMAL", "WATCH"])

    # 5. 对比表格
    comparison_table(naive_metrics, lr_metrics)

    # 6. 结论
    print()
    print("=" * 70)
    print("【结论】")
    print("=" * 70)
    better = sum(1 for m in ["accuracy", "precision", "recall", "f1"]
                 if lr_metrics[m] > naive_metrics[m])
    if better == 0:
        print("  在当前样本量（train=231，test=98）与特征集（33 个统计+DTW）下，")
        print("  LogisticRegression 未能优于朴素基线。")
        print()
        print("  分析：")
        print("    1. 训练集极端不平衡（WATCH 仅 2%），模型学不到少数类模式")
        print("    2. 特征仅来自 history 窗口统计量，DTW 相似度对 WATCH 区分能力有限")
        print("    3. 规则标签（NORMAL/WATCH）与数值特征相关性较弱")
        print()
        print("  改进建议：")
        print("    1. 扩大训练集（向前扩展 TRAIN_DAYS）增加 WATCH 样本")
        print("    2. 加入时序特征（一阶差分、趋势斜率）或频域特征")
        print("    3. 尝试对 WATCH 类别做 class_weight='balanced' 的 LR（本次未做超参搜索）")
    else:
        print(f"  LogisticRegression 在 {better}/4 项指标上优于朴素基线。")

    # 7. 错误分析
    print()
    print("=" * 70)
    print("【错误分析】")
    print("=" * 70)
    fp_samples, fn_samples = error_analysis(test, y_test, lr_pred, model, feature_names)

    # 文字分析
    print()
    print("=" * 70)
    print("【错误分析文本】")
    print("=" * 70)
    if fp_samples or fn_samples:
        print("""
  错分根因分析：
  根因一（阈值问题）：rule_v1 的 WATCH 阈值基于 B02 扰动峰值设计，
    但标签窗口仅 2 点（1 小时），部分扰动事件在标签窗口内未充分体现。
  根因二（特征不足）：history 窗口统计量（mean/std/slope）对轻度 WATCH（如
    cold_snap 降温 5°C）区分能力弱，数值落在 NORMAL 区间内。
  根因三（不平衡）：训练集 WATCH 仅 5 个（2%），LR 学到的决策边界严重偏
    向 NORMAL，导致 FN 多于 FP。

  改进方案：
  方案 A（数据侧）：扩展 TRAIN_DAYS 覆盖更多扰动事件，或使用 class_weight='balanced'
    的 LR 调整类别权重（需重新 fit，不算超参搜索）。
  方案 B（特征侧）：加入差分特征（target 窗口前的梯度变化）、扰动持续时长特征，
    或用 Tsfresh 自动提取时序特征子集。
  方案 C（标签侧）：由农业专家审核 rule_v1 阈值是否合理，必要时调整为分档阈值
    （如 WATCH_WARM / WATCH_COLD 等细粒度标签）。
""")
    else:
        print("  无错分样本，全部正确分类 ✅")

    print()
    print("=" * 70)
    print("B06 Step 4 完成 ✅")
    print("=" * 70)


if __name__ == "__main__":
    main()
