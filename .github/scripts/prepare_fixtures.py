#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""CI 专用：重建 B02 数据库与 B06 模型（复现冻结基线）

为什么需要这一步：
  `B02/greenmate.db` 与 `B06/artifacts/` 都在 `.gitignore` 里，干净检出时不存在，
  B07 的 S1–S5 不变量测试会直接失败（实测：40 failed + 6 errors）。

两者都可以确定性重建：
  - **数据库**：`GreenhouseSimulator(seed=42)`，起点对齐到冻结基线那一天的
    2026-10-15T02:00:00 → 336 点，逐行 sha256 与本地冻结数据一致。
    （模拟器默认用"当前时间"当起点，所以必须显式传 start_time。）
  - **模型**：`LogisticRegression(random_state=42)` → 保存出的 joblib 文件
    sha256 = aadc847a…，与 `B07/artifacts/b08_baseline.json` 记录的一致。

唯一不可复现的是模型清单里的 `saved_at`（保存时的墙钟时间），
因此 CI 里排除 `test_b08_freeze.py::test_model_fingerprints_match_real_files`
（它校验本地冻结文件的 sha256，在干净检出里无法通过）。
"""
from __future__ import annotations

import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[2]          # 仓库根目录
sys.path.insert(0, str(ROOT / "B02"))

from database.store import init_schema, row_count, write_samples   # noqa: E402
from simulator.engine import GreenhouseSimulator                    # noqa: E402

#: 冻结基线的时间线起点（本地 greenmate.db 的 MIN(timestamp)）
START_TIME = datetime(2026, 10, 15, 2, 0)
MODEL_PATH = "B06/artifacts/b06-20261020-v1"


def seed_database() -> None:
    """按固定 seed + 固定起点生成 336 点环境数据。"""
    init_schema()
    if row_count() > 0:
        print(f"[skip] 数据库已有 {row_count()} 条，不重复生成")
        return
    sim = GreenhouseSimulator(seed=42, start_time=START_TIME)
    samples = sim.generate()
    inserted = write_samples(samples)
    print(f"[ok] 数据库：{inserted} 条 "
          f"({samples[0].timestamp.isoformat()} ~ {samples[-1].timestamp.isoformat()})")


def train_model() -> None:
    """训练并保存 B06 模型（产物路径与冻结基线一致）。"""
    print(f"[run] 训练并保存模型：{MODEL_PATH}")
    subprocess.run(
        [sys.executable, "-m", "B06.persist", "save", "--model", MODEL_PATH],
        cwd=str(ROOT), check=True,
    )


def main() -> int:
    seed_database()
    train_model()
    return 0


if __name__ == "__main__":
    sys.exit(main())
