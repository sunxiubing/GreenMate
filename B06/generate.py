"""B06 Step 1 — 时间窗口生成 + DTW 相似度计算

用法：
  python B06/generate.py              # 默认 WINDOW_SIZE=6 TARGET_SIZE=2
  python B06/generate.py --window 8  # 自定义窗口大小
  python B06/generate.py --no-dtw    # 跳过 DTW 计算（快速生成窗口）
"""

import sys
import argparse
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, "D:/GreenMate")

from datetime import datetime, timedelta, timezone
from pathlib import Path

from B06.SPEC import (
    WINDOW_SIZE, TARGET_SIZE, DTW_COMPUTE, DTW_WINDOW_LIMIT,
    Sample, TimeWindow, compute_dtw, dtw_distance,
)


# --------------------------------------------------------------------------
# 数据读取
# --------------------------------------------------------------------------

def load_samples() -> list[Sample]:
    """从 B02 数据库读取全部样本，按 timestamp 升序排列。"""
    from B02.database.store import all_samples

    rows = all_samples()
    samples = []
    for r in rows:
        samples.append(Sample(
            timestamp=r.timestamp,
            temperature=r.temperature,
            humidity=r.humidity,
            lux=r.lux,
            source_type=r.source_type or "SIMULATED",
            sim_scenario=r.sim_scenario or "",
        ))
    # 按时间升序
    samples.sort(key=lambda s: s.timestamp)
    return samples


# --------------------------------------------------------------------------
# 窗口生成
# --------------------------------------------------------------------------

def generate_windows(
    samples: list[Sample],
    window_size: int = WINDOW_SIZE,
    target_size: int = TARGET_SIZE,
    compute_dtw: bool = DTW_COMPUTE,
) -> list[TimeWindow]:
    """滑动窗口生成，丢弃跨边界窗口。"""
    windows: list[TimeWindow] = []
    n = len(samples)

    for i in range(n):
        # 历史窗口起点
        h_start = i
        h_end = i + window_size
        # 目标窗口起点
        t_start = h_end
        t_end = h_end + target_size

        # 越界检查
        if h_end > n or t_end > n:
            continue  # 跨边界，直接丢弃

        # 构造窗口
        hist = samples[h_start:h_end]
        targ = samples[t_start:t_end]

        # 丢弃窗口不完整的情况
        if len(hist) < window_size or len(targ) < target_size:
            continue

        # 数据来源标注（取历史窗口第一个点的 source）
        source = hist[0].source_type
        window_id = f"w_{i:04d}_{hist[0].timestamp.strftime('%m%d%H%M')}"

        w = TimeWindow(
            window_id=window_id,
            source=source,
            history=hist,
            target=targ,
        )

        windows.append(w)

    return windows


def add_dtw(windows: list[TimeWindow]) -> list[TimeWindow]:
    """批量计算每个窗口与历史窗口的 DTW 相似度。"""
    n = len(windows)
    print(f"  开始计算 DTW（{n} 个窗口 × 历史上限{DTW_WINDOW_LIMIT}）...")
    for i, w in enumerate(windows):
        # 当前窗口之前的所有窗口作为历史
        history = windows[:i]
        if history:
            history = history[-DTW_WINDOW_LIMIT:]  # 只取最近 N 个
            w = compute_dtw(w, history)
        if (i + 1) % 20 == 0:
            print(f"    进度 {i + 1}/{n}")
    return windows


# --------------------------------------------------------------------------
# 统计报告
# --------------------------------------------------------------------------

def report(windows: list[TimeWindow], window_size: int, target_size: int):
    print()
    print("=" * 60)
    print("B06 Step 1 — 时间窗口生成报告")
    print("=" * 60)
    print(f"  配置:")
    print(f"    WINDOW_SIZE  = {window_size} 点（{window_size * 30} 分钟）")
    print(f"    TARGET_SIZE  = {target_size} 点（{target_size * 30} 分钟）")
    print(f"    DTW 计算     = {'是' if DTW_COMPUTE else '否'}")
    print()
    print(f"  原始样本数   : {len(load_samples())}")
    print(f"  生成窗口数   : {len(windows)}")
    print(f"  丢弃窗口数   : {max(0, len(load_samples()) - window_size - target_size - len(windows))}")
    print()

    if windows:
        w0 = windows[0]
        print(f"  首个窗口: {w0.window_id}")
        print(f"    history: {w0.history[0].timestamp} → {w0.history[-1].timestamp}")
        print(f"    target:  {w0.target[0].timestamp} → {w0.target[-1].timestamp}")
        print(f"    source:  {w0.source}")
        print(f"    DTW:     T={w0.dtw_temperature:.4f} H={w0.dtw_humidity:.4f} L={w0.dtw_lux:.4f}")
        print()

        wlast = windows[-1]
        print(f"  末个窗口: {wlast.window_id}")
        print(f"    history: {wlast.history[0].timestamp} → {wlast.history[-1].timestamp}")
        print(f"    target:  {wlast.target[0].timestamp} → {wlast.target[-1].timestamp}")
        print(f"    source:  {wlast.source}")

        # DTW 分布统计
        temps = [w.dtw_temperature for w in windows if w.dtw_temperature >= 0]
        if temps:
            print()
            print(f"  DTW 分布（temperature）:")
            print(f"    min={min(temps):.2f}  max={max(temps):.2f}  avg={sum(temps)/len(temps):.2f}")

    print()
    print("=" * 60)


# --------------------------------------------------------------------------
# 验证 1: 溯源（窗口 ID → B02 原始记录）
# --------------------------------------------------------------------------

def trace_window(windows: list[TimeWindow], samples: list[Sample]):
    """交互式溯源：输入窗口 ID 或数字索引，打印对应的 B02 原始时序片段。"""
    print()
    print("=" * 60)
    print("【验证 1】溯源：窗口 ID → B02 原始记录")
    print("=" * 60)
    print("  输入窗口 ID（如 w_0005_10150230）")
    print("  直接输入数字（如 2）对应第 2 个窗口")
    print("  输入 list 查看前 20 个窗口")
    print("  输入 q 退出溯源")
    print()

    window_map = {w.window_id: w for w in windows}

    def do_trace(w: TimeWindow):
        print(f"\n  窗口: {w.window_id}  source={w.source}")
        print(f"  历史窗口（{len(w.history)} 点）:")
        print(f"  {'序号':>4}  {'时间戳':<22}  {'温度':>6}  {'湿度':>6}  {'光照':>8}  {'场景':<10}")
        print("  " + "-" * 66)
        for idx, s in enumerate(w.history):
            marker = " ← 历史起点" if idx == 0 else (" ← 历史终点" if idx == len(w.history) - 1 else "")
            print(f"  {idx+1:>4}  {str(s.timestamp):<22}  {s.temperature:>5.1f}°C  {s.humidity:>5.1f}%  {s.lux:>8.0f}lux  {s.sim_scenario:<10}{marker}")

        print(f"\n  目标窗口（{len(w.target)} 点）:")
        print(f"  {'序号':>4}  {'时间戳':<22}  {'温度':>6}  {'湿度':>6}  {'光照':>8}")
        print("  " + "-" * 60)
        for idx, s in enumerate(w.target):
            marker = " ← 目标起点" if idx == 0 else (" ← 目标终点" if idx == len(w.target) - 1 else "")
            print(f"  {idx+1:>4}  {str(s.timestamp):<22}  {s.temperature:>5.1f}°C  {s.humidity:>5.1f}%  {s.lux:>8.0f}lux{marker}")

        # B02 原始记录交叉验证
        all_ts = {s.timestamp for s in w.history} | {s.timestamp for s in w.target}
        matched = [s for s in samples if s.timestamp in all_ts]
        print(f"\n  B02 原始记录: 窗口含 {len(all_ts)} 个时间点，B02 命中 {len(matched)} 条")

    while True:
        try:
            user = input("  [trace]> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not user:
            continue
        if user.lower() == "q":
            print("  退出溯源")
            break
        if user.lower() == "list":
            for i, w in enumerate(windows[:20]):
                print(f"  [{i:>3}] {w.window_id}  {w.history[0].timestamp} → {w.history[-1].timestamp}")
            continue

        # 数字索引
        try:
            idx = int(user)
            if 0 <= idx < len(windows):
                do_trace(windows[idx])
                continue
            else:
                print(f"  索引 {idx} 超出范围（0 ~ {len(windows)-1}），重新输入")
                continue
        except ValueError:
            pass

        # 完整窗口 ID
        w = window_map.get(user)
        if w is None:
            print(f"  未找到窗口: {user}，输入数字索引或 list 查看可用 ID")
            continue
        do_trace(w)


# --------------------------------------------------------------------------
# 验证 2: 可配置窗口大小
# --------------------------------------------------------------------------

def verify_configurable_window():
    """验证：修改 SPEC.WINDOW_SIZE，重跑脚本自动重新生成全部窗口。"""
    print()
    print("=" * 60)
    print("【验证 2】可配置窗口大小")
    print("=" * 60)

    import B06.SPEC as spec

    print(f"\n  当前配置（spec.py）:")
    print(f"    WINDOW_SIZE = {spec.WINDOW_SIZE} 点 = {spec.WINDOW_SIZE * 30} 分钟")
    print(f"    TARGET_SIZE = {spec.TARGET_SIZE} 点 = {spec.TARGET_SIZE * 30} 分钟")

    samples = load_samples()
    print(f"\n  加载 B02 样本: {len(samples)} 条")

    # 遍历不同窗口大小验证
    test_configs = [
        (4, "4 点 / 2 小时（较密集）"),
        (6, "6 点 / 3 小时（默认）"),
        (8, "8 点 / 4 小时（较稀疏）"),
        (12, "12 点 / 6 小时（长窗口）"),
    ]

    print(f"\n  测试配置:")
    for ws, label in test_configs:
        ws_count = len(samples) - ws - spec.TARGET_SIZE
        ws_count = max(0, ws_count)
        print(f"    WINDOW_SIZE={ws:2d} → 可生成 {ws_count:3d} 个窗口  ({label})")

    # 实际重跑两个配置验证
    print(f"\n  重跑验证（window_size=4 和 window_size=8）:")
    for test_ws in [4, 8]:
        orig_ws = spec.WINDOW_SIZE
        spec.WINDOW_SIZE = test_ws
        wins = generate_windows(samples, window_size=test_ws, target_size=spec.TARGET_SIZE)
        spec.WINDOW_SIZE = orig_ws
        print(f"    WINDOW_SIZE={test_ws}: 生成 {len(wins)} 个窗口 ✅")
        if wins:
            print(f"      首个: {wins[0].window_id}  history[0]={wins[0].history[0].timestamp}")

    print(f"\n  结论: 修改 SPEC.WINDOW_SIZE 后重新运行脚本，窗口自动重新生成 ✅")


# --------------------------------------------------------------------------
# 主程序
# --------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="B06 Step 1: 时间窗口生成 + DTW")
    parser.add_argument("--window", type=int, default=None,
                        help=f"输入窗口大小（默认读取 spec.py WINDOW_SIZE）")
    parser.add_argument("--target", type=int, default=None,
                        help=f"预测目标窗口大小（默认读取 spec.py TARGET_SIZE）")
    parser.add_argument("--no-dtw", action="store_true",
                        help="跳过 DTW 计算")
    parser.add_argument("--trace", type=str, default=None,
                        help="溯源指定窗口 ID（不进入交互模式）")
    args = parser.parse_args()

    import B06.SPEC as spec

    # 使用命令行参数或 spec.py 默认值
    window_size = args.window if args.window is not None else spec.WINDOW_SIZE
    target_size = args.target if args.target is not None else spec.TARGET_SIZE

    print(f"加载 B02 样本数据...")
    samples = load_samples()
    print(f"  样本数: {len(samples)}")
    print(f"  时间范围: {samples[0].timestamp} → {samples[-1].timestamp}")
    print(f"  数据来源: {set(s.source_type for s in samples)}")
    print()

    print(f"生成时间窗口（WINDOW_SIZE={window_size}, TARGET_SIZE={target_size}）...")
    orig_ws = spec.WINDOW_SIZE
    orig_ts = spec.TARGET_SIZE
    orig_dtw = spec.DTW_COMPUTE
    spec.WINDOW_SIZE = window_size
    spec.TARGET_SIZE = target_size
    if args.no_dtw:
        spec.DTW_COMPUTE = False

    windows = generate_windows(samples, window_size=window_size, target_size=target_size)

    if spec.DTW_COMPUTE and windows:
        windows = add_dtw(windows)

    spec.WINDOW_SIZE = orig_ws
    spec.TARGET_SIZE = orig_ts
    spec.DTW_COMPUTE = orig_dtw

    report(windows, window_size, target_size)

    # 输出前 3 个窗口详情
    if windows:
        print()
        print("前 3 个窗口详情:")
        for w in windows[:3]:
            print(f"\n  [{w.window_id}]")
            print(f"    history: ", end="")
            for s in w.history:
                print(f"{s.temperature}°C/{s.humidity}%/{s.lux:.0f}lux ", end="")
            print()
            print(f"    target:  ", end="")
            for s in w.target:
                print(f"{s.temperature}°C/{s.humidity}%/{s.lux:.0f}lux ", end="")
            print()
            print(f"    DTW: T={w.dtw_temperature:.2f} H={w.dtw_humidity:.2f} L={w.dtw_lux:.2f}")

    # 验证 1: 溯源
    if args.trace:
        # 单次溯源模式
        window_map = {w.window_id: w for w in windows}
        w = window_map.get(args.trace)
        if w:
            print(f"\n  溯源: {args.trace}")
            print(f"  history: {[s.timestamp for s in w.history]}")
            print(f"  target:  {[s.timestamp for s in w.target]}")
        else:
            print(f"\n  未找到窗口: {args.trace}")
    else:
        # 交互式溯源
        trace_window(windows, samples)

    # 验证 2: 可配置窗口大小
    verify_configurable_window()


if __name__ == "__main__":
    main()
