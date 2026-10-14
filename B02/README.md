# GreenMate B02 — 数据记忆

把 B01 的单点状态升级为可持久查询的 7 天时序数据；程序重启后历史记录仍在，可查询、统计、检测变点。

> 数据资产供 B06 时间窗口任务复用；变点监测算法为 B07 状态演化分析提供边界。

---

## 目录结构

```
B02/
├── SPEC.py                 # 字段规范 / 时间轴 / sim_scenario 使用限制
├── README.md               # 本文档
├── requirements.txt
├── greenmate.db            # SQLite 数据库（已 commit，gitignore）
│
├── simulator/
│   ├── __init__.py
│   └── engine.py           # 7天 × 336点时序数据生成器
│
├── database/
│   ├── __init__.py
│   ├── store.py            # SQLite 持久化（建表 / 写入 / 校验）
│   └── generate.py         # 主程序入口（生成 + 写入 + 报告）
│
├── api/
│   ├── __init__.py
│   ├── history.py          # B07 预留接口（get_current / get_history）
│   └── server.py           # FastAPI 时序查询服务（端口 8001）
│
├── quality/
│   └── checker.py          # 质量检查（空值 / 重复 / 范围 / 连续性 / 分布）
│
├── changepoint/
│   └── detector.py         # 变点监测算法（滚动均值自适应阈值）
│
└── static/
    └── history.html        # Chart.js 时序曲线页面
```

---

## 快速开始

### 1. 安装依赖（与 B01 共用 venv）

```bash
cd B02
../B01/venv/Scripts/python.exe -m pip install fastapi uvicorn pydantic
```

### 2. 生成并持久化数据

```bash
../B01/venv/Scripts/python.exe database/generate.py        # 如有旧数据则跳过
../B01/venv/Scripts/python.exe database/generate.py --force  # 强制重刷
```

输出示例：
```
[15:27:24] B02 数据生成器启动
  数据库: D:\GreenMate\B02\greenmate.db
  预期样本: 336 条 (7 天 × 48 点/天)
  生成完成: 336 条
  写入完成: 336 条
  数量校验: ✓ 336 条（符合预期）
  顺序校验: ✓ 时间顺序正确
  前半段含扰动: True  后半段含扰动: True
```

### 3. 质量检查

```bash
../B01/venv/Scripts/python.exe -m quality.checker
```

6 项检查全部 PASS 方可继续。

### 4. 启动时序查询服务

```bash
../B01/venv/Scripts/python.exe -m uvicorn api.server:app --host 127.0.0.1 --port 8001
```

浏览器打开 **http://127.0.0.1:8001/**

### 5. B07 接口（不经过 HTTP，直接调用）

```python
from api.history import get_current_environment, get_environment_history, query_stats
from datetime import datetime, timedelta

# 最新一条
cur = get_current_environment()
print(cur.temperature, cur.humidity, cur.lux, cur.sim_scenario)

# 近 3 天数据
end = datetime.now()
start = end - timedelta(days=3)
samples = get_environment_history(start, end)

# 统计摘要
stats = query_stats(start, end)
print(stats["temperature"], stats["trend"], stats["scenario_counts"])
```

---

## 时间轴

| 参数 | 值 |
|---|---|
| 采样间隔 | 30 分钟 |
| 时间范围 | 7 天 |
| 采样点数 | 336 |
| 起始时间 | 程序首次生成时自动确定（对齐 30 分钟整点）|

---

## 数据库字段

表：`environment_samples`

| 字段 | 类型 | 说明 |
|---|---|---|
| id | INTEGER | 主键，自增 |
| timestamp | DATETIME | ISO 8601，精确到秒 |
| temperature | REAL | °C，范围 -50 ~ 80 |
| humidity | REAL | %，范围 0 ~ 100 |
| lux | REAL | lux，范围 0 ~ 200000 |
| source_type | TEXT | 当前固定为 `simulated` |
| sim_scenario | TEXT | 场景名（见下方限制）|

---

## sim_scenario 使用限制

`sim_scenario` 是**模拟器内部元数据**，不是真实农情标签：

**禁止**：
- 直接作为"正常/异常"标签用于模型训练或业务决策
- 依赖它做报警判断

**允许**：
- 调试追溯：这条数据是哪个场景生成的
- 回归验证：模拟器行为是否符合预期
- 二次标注：人工审核后替换为真实农情标签

**原因**：模拟器的"heat_wave"在某些作物某些时段（如果树休眠期）恰恰是正常农情，不能直接映射为异常。真实标签应由 B06 阶段由农业专家构造。

---

## 变点监测算法（创新点）

**输入**：温度时序序列（Sample 列表或数据库全量）
**输出**：状态突变时间点列表（ChangePoint）

**算法**：局部自适应阈值

1. 取 12 点（约 6 小时）滚动窗口
2. 计算窗口内温度均值 `μ` 和标准差 `σ`
3. 当前温度偏离 `μ` 超过 `max(2°C, 2σ)` → 触发变点
4. 两次变点至少间隔 16 点（约 8 小时）

**局限性**：
- 扰动强度弱、持续时间长（cold_snap）：偏差峰值 -5°C，低于固定阈值，可能漏报
- 昼夜正常升温/降温本身也触发检测（属正常生理现象，非误报）
- 评估基准是 sim_scenario 切换点，两者定义不同，不宜作为算法准确率的直接度量

**用途**：为 B06 提供窗口边界候选，人工审核后确定最终标签。

```python
from changepoint.detector import detect_changepoints, evaluate_detection
from database.store import all_samples

samples = all_samples()
cps = detect_changepoints(samples)
print(f"检测到 {len(cps)} 个变点")
for cp in cps:
    print(f"  {cp.timestamp}  ΔT={cp.delta_temp:+.1f}°C  场景={cp.new_scenario}")

# 与 sim_scenario 对照评估（仅用于算法调试）
eval_result = evaluate_detection(cps, samples)
print(f"recall={eval_result['recall']} precision={eval_result['precision']}")
```

---

## B06 如何复用这些数据

B06 时间窗口预测任务需要构造训练样本，每个样本 = 一个时间窗口 + 标签。

**窗口构造方式**：

```python
from api.history import get_environment_history
from changepoint.detector import detect_changepoints

cps = detect_changepoints()   # 变点 = 窗口边界候选
# 取每个变点前后各 WINDOW_SIZE 个样本构成窗口
# 标签由专家根据窗口内场景分布判定（不是直接用 sim_scenario）
```

**数据复用要点**：

- 336 条样本来自 7 天完整周期，包含正常和扰动片段
- 同一 `seed=42` 每次生成同一序列，可复现
- 重启后数据不丢失，可追加新样本（续接时间线）
- 扰动分散在前半段和后半段，支持"训练集含扰动/测试集含扰动"的回归验证

---

## 扰动片段清单

| 场景 | 持续时长 | 进入/恢复 | 主要数值偏移 |
|---|---|---|---|
| heat_wave | 18 小时 | 各 4 小时 | 温度 +12°C，湿度 -25% |
| drought | 24 小时 | 各 6 小时 | 温度 +6°C，湿度 -30% |
| shade | 12 小时 | 各 3 小时 | 光照 -40000 lux |
| cold_snap | 20 小时 | 各 5 小时 | 温度 -10°C，湿度 +15% |
| flood | 16 小时 | 各 4 小时 | 湿度 +20%，光照 -25000 lux |

---

## B08 回归验证要点

- **时间线可复现**：种子固定为 42，同一 seed 产生同一 336 点序列
- **扰动分散**：前半段（0-168 点）有 heat_wave/drought/shade，后半段（168-336 点）有 cold_snap/flood
- **重启数据不丢**：数据库已 commit，关闭再打开数据完整
- **变点边界稳定**：用同一算法重跑应得到相同变点列表

---

## 已知局限

1. **数据是合成的**：无真实传感器，sim_scenario 不代表真实农情
2. **采样间隔固定为 30 分钟**：不适配需要更高频数据的场景
3. **变点算法依赖温度信号**：cold_snap 等弱扰动可能漏检，湿度/光照联合检测待扩展
4. **无并发控制**：API 层无流量限制，不适合生产环境直接暴露
5. **无增量更新**：新数据只能全量重刷或手动 append，不支持实时流写入
