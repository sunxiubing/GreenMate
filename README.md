# GreenMate — 大棚环境监测系统

把【当前大棚状态 + 用户问题】一起发给 LLM，用**同题 A/B 对照**证明 AI 真的读到了当前状态；
同时把单点状态升级为可持久查询的 7 天时序数据，支持统计、变点检测和时间窗口预测。

> ⚠️ 所有传感器数值均为**模拟数据（SIMULATED）**，未接入任何真实硬件。

---

## 项目结构

```
GreenMate/
├── .gitignore
├── README.md              # 本文档
├── requirements.txt       # 统一依赖（pip install -r requirements.txt）
│
├── B01/                   # LLM 状态上下文对照
│   ├── main.py            # FastAPI：状态定义、页面路由、A/B 对照接口
│   ├── llm.py             # 提示词、状态注入、配置日志、异常翻译
│   ├── check_llm.py       # 独立验证 API Key / Base URL / 模型名
│   ├── verify_step5.py    # 故障注入验收脚本
│   ├── static/
│   │   └── index.html     # 页面：A/B 双面板、可编辑数值、Context 展开
│   ├── .env               # 密钥（已移至根目录，gitignore 排除）
│   └── venv/              # 已移除，统一使用根目录 venv/
│
├── B02/                   # 数据记忆：时序持久化 + 变点检测
│   ├── SPEC.py            # 字段规范 / 时间轴 / sim_scenario 使用限制
│   ├── simulator/
│   │   └── engine.py      # 7天 × 336点时序数据生成器（GreenhouseSimulator）
│   ├── database/
│   │   ├── store.py       # SQLite 持久化（建表 / 写入 / 校验）
│   │   └── generate.py    # 主程序入口（生成 + 写入 + 报告）
│   ├── api/
│   │   ├── history.py     # 直接调用，不过 HTTP
│   │   └── server.py      # FastAPI 时序查询服务（端口 8001）
│   ├── quality/
│   │   └── checker.py     # 6 项质量检查
│   ├── changepoint/
│   │   └── detector.py    # 变点监测算法
│   ├── static/
│   │   └── history.html   # Chart.js 时序曲线页面
│   ├── gen_sample.py      # 生成 20 条样本写入 env_data 表
│   ├── query_sample.py    # 查询 / 验证 env_data 表
│   ├── check_disturbance.py  # 查询扰动数据
│   ├── __init__.py
│   └── greenmate.db       # SQLite 数据库（已 commit，gitignore）
│
├── B03/                   # 气象 API 接入：外部环境证据 + 卡尔曼滤波
│   ├── SPEC.py            # API 文档 / 预置城市坐标 / WMO 天气代码
│   ├── weather.py         # 最小真实调用：打印 HTTP status + 原始 JSON
│   ├── weather_service.py # 主程序：PUBLIC/SIMULATED 对比 + 卡尔曼滤波 + 可视化
│   └── .env.example       # 配置模板（终端传参优先级高于此文件）
│
├── B04/                   # 检索增强知识库问答：带溯源引用的 RAG
│   ├── SPEC.py            # 知识库问题 / 可信资料 / 切片定义
│   ├── retrieve.py        # TF-IDF 向量检索（纯 Python，无需大模型 API）
│   ├── answer.py          # Step 5-6：回答生成 + 边界幻觉测试
│   └── __pycache__/       # Python 缓存（gitignore）
│
├── B05/                   # 工具调用编排器 + SoilGrids MCP
│   ├── orchestrator.py    # ReAct 流水线：参数验证 / 超时保护 / unavailable 重试
│   ├── tools.py           # OpenAI function-calling 工具定义（4 个工具）
│   ├── test_functions.py  # 四个底层业务函数测试（ok/unavailable/error 三态）
│   ├── test_connect_error.py   # 网络异常注入测试
│   ├── test_multi_tool.py      # 多工具任务验证（五步日志格式）
│   ├── test_orchestrator.py    # 编排器 ask() 主流程测试
│   ├── test_boundary.py        # 边界幻觉测试
│   ├── test_soilgrids.py       # SoilGrids MCP 五步 + Step 6 手动交互
│   ├── verify_weather_source.py # 气象数据源验证
│   └── run_all_b05.py     # 全量测试串行执行器
│
├── B06/                   # 实时窗口推理 + SHAP 贡献
│   ├── SPEC.py            # 时间窗口结构 + DTW 算法（纯 Python）
│   ├── config.py          # 特征白名单、标签阈值、DTW 配置
│   ├── generate.py        # 窗口构造 + trace CLI（WINDOW_SIZE=6 可配置）
│   ├── feature.py         # 33 特征提取（仅从 history 窗口）
│   ├── split.py           # 时间切分 + 泄漏自检（4 项检查）
│   ├── train.py           # 朴素基线 + LogisticRegression 训练评估
│   ├── persist/           # 模型持久化
│   │   ├── persist.py     # save_model / load_model（含一致性校验）
│   │   └── __main__.py    # CLI：save / load
│   └── inference/         # 实时推理
│       ├── inference.py   # predict_window / MODEL_OUTPUT / SHAP / DTW
│       └── __main__.py    # CLI：infer / verify
│
├── B07/                   # S1 稳态基线 / S2 渐进漂移：选窗 → 取数 → 推理 → 判断 → 解释 → 存档
│   ├── __init__.py        # 包声明（让 python -m B07.runs 能跑）
│   ├── __main__.py        # 入口分发：默认 S1 全流程，--scenario S1/S2，list/explain 透传给 runs
│   ├── s1.py              # S1 统一入口 + 阶段报告生成
│   ├── s2.py              # S2 统一入口（渐进漂移）+ 阶段报告生成
│   ├── scenarios.py       # 场景注册表（S1/S2）：哪个场景用哪个选窗策略
│   ├── check_s2_experiment.py  # S2 实验有效性校验脚本（只读存档，9 条自检）
│   ├── config.py          # 位置 / 窗口点数 / 采样间隔 / gap 阈值
│   ├── stability.py       # 稳定窗口扫描 + 透明稳定判定规则
│   ├── evidence.py        # 当前状态 + 最近历史 + 时间锚点（含校验）
│   ├── predict.py         # 加载 B06 已存模型做一次预测
│   ├── judgement.py       # 结构化判断五/六字段 + 合成关注等级（纯程序，无 LLM）
│   ├── explain.py         # LLM 解释 + 一致性校验（只解释，不判断）
│   ├── runs.py            # run 存档 + index 索引 + explain_run 回溯 + rebuild_index
│   ├── weather.py         # S3 天气证据接入层（B03 未来外部天气，走 B05 工具接口）
│   ├── s3.py              # S3-2 单变量对照实验（真实组 / 离线组 / 降级验证）
│   ├── s3_report.py       # 从实测存档生成 s3_report.md
│   ├── s3_citation_probe.py   # 引用率探针（冻结 ⑥ 块重复跑，只测 LLM 采样波动）
│   ├── s3_citation_table.py   # 全部 S3_with 存档的统一判定表
│   ├── tests/             # S1/S2/S3 不变量测试（pytest）
│   │   ├── conftest.py    # 路径注入 + 假 LLM 客户端
│   │   ├── test_s1_invariants.py
│   │   ├── test_s2_invariants.py
│   │   ├── test_weather_invariants.py
│   │   └── test_s3_invariants.py
│   ├── runs/              # 运行时生成的存档（gitignore *.json）
│   │   ├── index.json     # 索引：run_id → 摘要
│   │   └── run_XXX/run.json
│   └── artifacts/         # 阶段报告
│       ├── s1_report.md
│       ├── s2_report.md
│       ├── s3_report.md
│       ├── s3_raw_result.json
│       ├── s3_offline_result.json
│       ├── s3_citation_stats.json
│       ├── s3_citation_table.json
│       ├── s3_frozen_ext.json
│       └── s3_calibration.json
│
└── docs/                  # 项目设计文档
    ├── b01_b03_design.md  # SIMULATED/PUBLIC 数据源定义
    └── b03_weather.md     # HTTP 400 缓存策略 / Fail Loudly 设计
```

---

## 环境要求

- **Python 3.12**（开发验证于 3.12.7）
- 统一依赖安装：`pip install -r requirements.txt`
- B01 额外需要：一个 OpenAI 兼容的 LLM 服务（本项目用阿里云百炼 MaaS 工作空间）

---

## 快速开始

所有命令在 **项目根目录** `D:\GreenMate>` 下直接运行，无需 cd 进入子文件夹。

### 前置命令

**激活虚拟环境（Windows CMD）**
```cmd
D:\GreenMate> venv\Scripts\activate.bat
```

**清理端口占用（可选，端口被占时用）**
```cmd
:: 查端口占用
netstat -ano | findstr :8000
netstat -ano | findstr :8001

:: 杀进程（替换 <PID> 为实际进程号）
taskkill /F /PID <PID>
```

---

### B01 — LLM 状态上下文对照（端口 8000）

**1. 启动服务**

```cmd
D:\GreenMate> venv\Scripts\activate.bat
(venv) D:\GreenMate> python -m uvicorn B01.main:app --host 127.0.0.1 --port 8000
```

**启动成功标志：** `Uvicorn running on http://127.0.0.1:8000`

**浏览器访问：** <http://127.0.0.1:8000/>

**2. 配置 LLM（如未配置）**

编辑根目录 `.env` 文件：
```
LLM_API_KEY=sk-你的密钥
LLM_BASE_URL=https://<你的工作空间>.cn-beijing.maas.aliyuncs.com/compatible-mode/v1
LLM_MODEL=qwen3.5-27b
```

修改后需重启 B01 服务。

**3. 验证 LLM 配置**

```cmd
(venv) D:\GreenMate> python B01/check_llm.py
```

---

### B02 — 数据记忆（端口 8001）

**1. 生成 7 天时序数据（336 条）**

```cmd
(venv) D:\GreenMate> python B02/database/generate.py        # 如有旧数据则跳过
(venv) D:\GreenMate> python B02/database/generate.py --force  # 强制重刷
```

**2. 启动时序查询服务**

```cmd
(venv) D:\GreenMate> python -m uvicorn B02.api.server:app --host 127.0.0.1 --port 8001
```

**启动成功标志：** `Uvicorn running on http://127.0.0.1:8001`

**浏览器访问：** <http://127.0.0.1:8001/>

**3. 查询数据**

```cmd
:: 查看全部数据（environment_samples 表，336 条）
(venv) D:\GreenMate> python B02/check_disturbance.py

:: 查看最近 N 条
(venv) D:\GreenMate> python B02/query_sample.py --last 10

:: 查看全部（env_data 表，gen_sample 生成的）
(venv) D:\GreenMate> python B02/query_sample.py

:: 清空 env_data 表
(venv) D:\GreenMate> python B02/query_sample.py --clear

:: 质量检查（6 项全部 PASS 才可继续）
(venv) D:\GreenMate> python -m B02.quality.checker
```

**4. B07 接口（Python 代码调用，不过 HTTP）**

```python
import sys; sys.path.insert(0, 'D:/GreenMate')
from B02.api.history import get_current_environment, get_environment_history, query_stats
from datetime import datetime, timedelta

cur = get_current_environment()
print(cur.temperature, cur.humidity, cur.lux)

end = datetime.now()
start = end - timedelta(days=3)
samples = get_environment_history(start, end)
stats = query_stats(start, end)
print(stats["temperature"], stats["trend"])
```

---

### 常见报错

| 报错 | 原因 | 解决 |
|------|------|------|
| `ModuleNotFoundError` | 未激活 venv | 先运行 `venv\Scripts\activate.bat` |
| `Port already in use` | 端口被占 | 杀旧进程或换端口 `--port 8002` |
| 改了 `.env` 没生效 | 进程未重启 | 重启 B01 服务 |
| 页面空白/数据加载失败 | 服务未启动或缓存 | Ctrl+Shift+R 强制刷新，重启服务 |

---

## B01 核心链是怎么被证明的

A/B 对照是一次**控制变量实验**：

| 变量 | State A | State B |
|---|---|---|
| 用户问题 | 当前环境怎么样？ | **完全相同** |
| System Prompt | 同一份 | **完全相同** |
| 模型 / 温度 / 参数 | 相同 | **相同** |
| **注入的当前状态** | 24°C / 65% / 12000lux | 36°C / 35% / 22000lux ← **唯一变量** |

因为唯一的差异是状态，两次回答的差异**只可能**来自状态。

> 如果状态变了回答却基本不变，**先检查 Context 有没有真的更新**（展开「本次注入的 Context」看），而不是先去改 Prompt。

---

## B02 时间轴与数据规格

| 参数 | 值 |
|---|---|
| 采样间隔 | 30 分钟 |
| 时间范围 | 7 天 |
| 采样点数 | 336 |
| 扰动片段 | 5 段，分散分布在前/后半段 |
| 重启后数据 | 不丢失（SQLite WAL 模式）|

### 数据库字段

表：`environment_samples`

| 字段 | 类型 | 说明 |
|---|---|---|
| id | INTEGER | 主键，自增 |
| timestamp | DATETIME | ISO 8601，精确到秒 |
| temperature | REAL | °C |
| humidity | REAL | % |
| lux | REAL | lux |
| source_type | TEXT | 当前固定为 `simulated` |
| sim_scenario | TEXT | 场景名（**见下方限制**）|

### sim_scenario 使用限制（重要）

`sim_scenario` 是**模拟器内部元数据**，不是真实农情标签：

- **禁止**作为"正常/异常"标签用于模型训练或业务决策
- **允许**调试追溯、回归验证、二次标注（人工审核后替换）

**原因**：模拟器的"heat_wave"在某些作物某些时段恰恰是正常农情，不能直接映射为异常。
真实标签应由 B06 阶段由农业专家构造。

### 扰动片段清单

| 场景 | 持续时长 | 进入/恢复 | 主要数值偏移 |
|---|---|---|---|
| heat_wave | 18 小时 | 各 4 小时 | 温度 +12°C，湿度 -25% |
| drought | 24 小时 | 各 6 小时 | 温度 +6°C，湿度 -30% |
| shade | 12 小时 | 各 3 小时 | 光照 -40000 lux |
| cold_snap | 20 小时 | 各 5 小时 | 温度 -10°C，湿度 +15% |
| flood | 16 小时 | 各 4 小时 | 湿度 +20%，光照 -25000 lux |

---

## 变点监测算法（创新点，B02）

**输入**：温度时序序列
**输出**：状态突变时间点列表（ChangePoint）

**算法**：局部自适应阈值

1. 取 12 点（约 6 小时）滚动窗口
2. 计算窗口内温度均值 `μ` 和标准差 `σ`
3. 当前温度偏离 `μ` 超过 `max(2°C, 2σ)` → 触发变点
4. 两次变点至少间隔 16 点（约 8 小时）

```python
from changepoint.detector import detect_changepoints
from database.store import all_samples

cps = detect_changepoints(all_samples())
for cp in cps:
    print(f"{cp.timestamp}  ΔT={cp.delta_temp:+.1f}°C  场景={cp.new_scenario}")
```

**局限性**：
- cold_snap 等弱扰动（偏差峰值 -5°C）可能漏检
- 昼夜正常升温/降温也被捕捉（属正常生理现象，非误报）
- 评估基准是 sim_scenario 切换点，两者定义不同，不宜直接度量算法准确率

**用途**：为 B06 提供窗口边界候选，人工审核后确定最终标签。

---

## B06 如何复用 B02 数据

每个训练样本 = 一个时间窗口 + 标签。

```python
from api.history import get_environment_history
from changepoint.detector import detect_changepoints

cps = detect_changepoints()   # 变点 = 窗口边界候选
# 取每个变点前后各 WINDOW_SIZE 个样本构成窗口
# 标签由专家根据窗口内场景分布判定（不是直接用 sim_scenario）
```

---

## B08 回归验证要点

- **时间线可复现**：种子固定为 42，同一 seed 产生同一 336 点序列
- **扰动分散**：前半段（0-168 点）有 heat_wave/drought/shade，后半段有 cold_snap/flood
- **重启数据不丢**：数据库已 commit，关闭再打开数据完整
- **变点边界稳定**：用同一算法重跑应得到相同变点列表

---

## 常见问题

### 改了 `.env` 但完全没生效

**两个原因：**

1. **没保存**。看编辑器标签页：小圆点 `●` = 未保存，`×` = 已保存。最硬确认方式是看**文件大小变了没**。
2. **没重启服务**。`.env` 只在进程启动时读一次，之后改文件对运行中的进程毫无影响。

### 端口被占

```bash
netstat -ano | findstr :8000
taskkill //F //PID <PID>
```

### 中文日志乱码

`main.py` 顶部已加 `sys.stdout.reconfigure(encoding="utf-8", errors="replace")`。

### 页面改了但浏览器还是旧的

HTML 响应已带 `Cache-Control: no-cache`，仍不对就 **Ctrl+Shift+R** 强制刷新。

---

## 已知限制

1. **数据是模拟的**。没有真实传感器，`SIMULATED` 标注在页面上到处都有。
2. **A/B 机制证明的是「模型读到了状态」，不是「模型判断正确」**。机制有效，不保证农业判断准确。
3. **sim_scenario 不是真实农情标签**，不能直接用于业务决策。
4. **变点算法依赖温度信号**：cold_snap 等弱扰动可能漏检。
5. **无并发控制**：API 层无流量限制，不适合生产环境直接暴露。
6. **B02 无增量更新**：新数据只能全量重刷或手动 append，不支持实时流写入。
7. **B01 和 B02 暂无关联**：两者独立运行，数据不互通，未来计划由 B01 向 B02 写入实时状态。
8. **B03 Open-Meteo 数据为预报非实测**：降水概率等字段为概率预测，夜间太阳辐射为 0，非测量值。

---

## B04 — 检索增强知识库问答

实现带溯源引用的 RAG（Retrieval-Augmented Generation）。

**运行命令**

```cmd
(venv) D:\GreenMate> python B04/answer.py
```

**测试用例**

| ID | 类型 | 说明 |
|----|------|------|
| KB-001 | 业务问答 | SIMULATED 与 PUBLIC 数据源的本质区别，A/B 实验选取规则 |
| KB-002 | 业务问答 | drought 的 humidity_delta 偏移量及服务重启要求 |
| KB-003 | 业务问答 | HTTP 400 不返回旧缓存的原因及 Fail Loudly 原则 |
| KB-004 | 边界幻觉测试 | 告警音频文件名（知识库不存在，模型拒绝编造） |
| KB-005 | 边界幻觉测试 | PWM 工作频率（知识库不存在，模型拒绝编造） |

**技术方案**

- 向量检索：TF-IDF + cosine similarity（纯 Python，无需大模型 API）
- 知识库来源：项目文档切片（docs/ + 源码），元数据包含 source_id / section / page
- 引用约束：回答仅可使用本次检索命中的 chunk_id，禁止编造来源

---

## B05 — 工具调用编排器 + SoilGrids MCP

ReAct 风格的 LLM 工具编排流水线，支持多工具串联调用与失败验证。

**运行命令**

```cmd
(venv) D:\GreenMate> python B05/run_all_b05.py        # 全量测试串行执行
(venv) D:\GreenMate> python B05/test_soilgrids.py     # 单独运行 SoilGrids Step 6 手动交互
```

**核心模块**

| 文件 | 说明 |
|------|------|
| `orchestrator.py` | 编排器主流程：ReAct loop、Schema 验证、超时保护、unavailable 重试 |
| `tools.py` | OpenAI function-calling 工具定义（4 个底层工具） |
| `test_functions.py` | 四个底层业务函数（ok/unavailable/error 三态） |
| `test_connect_error.py` | 网络异常注入测试 |
| `test_multi_tool.py` | 多工具任务验证（五步日志格式） |
| `test_orchestrator.py` | 编排器 ask() 主流程测试 |
| `test_boundary.py` | 边界幻觉测试 |
| `test_soilgrids.py` | SoilGrids MCP 五步 + Step 6 失败验证与手动交互 |
| `run_all_b05.py` | 全量测试串行执行器 |

**编排器架构（orchestrator.py 重构）**

- **参数验证**：`validate_tool_args()` 解析 TOOLS schema，LLM 参数错误返回明确错误信息，不静默失败
- **超时保护**：`_exec_with_timeout()` 线程包装器，10s 超时强制终止
- **unavailable 重试**：工具返回 `status: "unavailable"` 时自动重试一次
- **max_turns 耗尽保护**：返回已执行工具摘要，而非静态字符串

**四步流水线**

1. 用户输入自然语言任务（禁止硬编码工具顺序）
2. LLM 判断调用哪个工具、传什么参数（`tool_choice="auto"`）
3. 后端解析 tool call → Schema 验证 → 超时保护执行 → unavailable 重试 → 返回真实结果
4. 结果注入消息上下文，LLM 生成最终回答

**工具清单（tools.py）**

| 工具名 | 功能 | 数据来源 |
|--------|------|---------|
| `get_current_environment` | 最新环境样本 | B02 SQLite |
| `get_environment_history` | 最近 N 小时时序 | B02 SQLite |
| `get_weather_forecast` | 天气预报 | Open-Meteo API |
| `search_tomato_knowledge` | 番茄知识库检索 | B04 TF-IDF |

**外部 MCP 接入（Step 5）**

SoilGrids MCP（`https://gateway.pipeworx.io/soilgrids/mcp`）补齐土壤数据缺口：

| 工具名 | 功能 |
|--------|------|
| `soil_properties` | pH / 有机碳 / 黏粒 / 砂粒 / 含水量 |
| `soil_classification` | FAO/WRB 世界土壤分类 |
| `list_soil_properties` | 可用属性列表 |

串联路径：`Open-Meteo(气象) + SoilGrids(土壤) → 精准灌溉策略`

**Step 6 失败验证（三类证据）**

| 场景 | 触发方式 | 证据 |
|------|---------|------|
| 参数错误 | latitude=999 | error="latitude must be between -90 and 90" |
| 超时 | timeout=0.001s | error="tool execution timeout after Xs" |
| 空结果 | 海洋坐标 | properties 全为 null |

## B03 — 天气 API 接入

接入 Open-Meteo 免费天气 API，获取真实外部环境证据。

**1. 运行命令**

```cmd
(venv) D:\GreenMate> python B03/weather_service.py
```

**2. 运行命令（卡尔曼滤波 + 可视化）**

```cmd
(venv) D:\GreenMate> python B03/weather_service.py
```

**3. B01 接入（通过 API）**

```cmd
(venv) D:\GreenMate> curl -X POST http://127.0.0.1:8000/api/weather/current -H "Content-Type: application/json" -d "{\"location\":\"北京大兴\",\"forecast_window\":\"now\"}"
```

**参数变更验证步骤**

| 测试 | 变更参数 | 预期结果 |
|------|---------|---------|
| 测试A | location=南京, forecast_window=6h | 返回多条 hourly 预报记录 |
| 测试B | location=(32.06, 118.79) 元组 | 按经纬度查询，数据与北京不同 |
| 失败验证 | latitude=999 | HTTP 400，系统拒绝返回旧缓存 |

**B03 功能清单**

| 功能 | 说明 |
|------|------|
| WeatherEvidence 结构 | temperature / humidity / precipitation_probability / cloud_cover / solar_radiation |
| 两类数据源 | PUBLIC（Open-Meteo）+ SIMULATED（B02 本地模拟） |
| 卡尔曼滤波 | 1D 卡尔曼滤波平滑温度时序，抖动抑制率 ~40% |
| 可视化 | matplotlib 对比曲线：蓝色原始预报 vs 红色滤波后 |
| 失败可观测 | HTTP 400/429/timeout 触发时返回值 None，无静默降级 |
| LLM 上下文 | format_state() 自动区分 PUBLIC 和 SIMULATED 来源 |

---

## 开发进度

| 模块 | 状态 |
|---|---|
| B01 — LLM 状态上下文对照 | ✅ 完成 |
| B02 — 数据记忆：时序持久化 + 变点检测 | ✅ 完成 |
| B03 — 天气 API 接入 + 卡尔曼滤波 | ✅ 完成 |
| B05 — 工具调用编排器 + SoilGrids MCP | ✅ 完成 |
| B04 — 检索增强知识库问答 | ✅ 完成 |
| B06 — 实时窗口推理 + SHAP 贡献 | ✅ 完成 |
| B07 — S1 稳态基线：选窗 → 取数 → 推理 → 判断 → 解释 → 存档 | ✅ 完成 |
| B07 — S2 渐进漂移：同一流程，只换历史窗口 | ✅ 完成 |
| B07 — S3 天气证据接入层：接 B03 未来外部天气（走 B05 工具接口） | ✅ 完成 |

---

## B06 — 实时窗口推理

基于 B02 时序数据和 B06 训练好的 LogisticRegression 模型，对实时窗口进行推理，输出预测结果和 SHAP 特征贡献。

**运行命令**

```cmd
(venv) D:\GreenMate> python B06/inference/inference.py infer --model B06/artifacts/b06-20261020-v1
(venv) D:\GreenMate> python B06/inference/inference.py verify --model B06/artifacts/b06-20261020-v1
(venv) D:\GreenMate> python B06/persist/persist.py save --model B06/artifacts/b06-20261020-v1
(venv) D:\GreenMate> python B06/persist/persist.py load --model B06/artifacts/b06-20261020-v1
```

**核心模块**

| 文件 | 说明 |
|------|------|
| `inference/inference.py` | 核心推理模块：DTW + 特征提取 + LR推理 + SHAP 贡献 |
| `inference/__main__.py` | CLI 入口 |
| `persist/persist.py` | 模型保存与重载（含一致性校验） |
| `SPEC.py` | 时间窗口结构 + DTW 算法 |
| `config.py` | 特征列白名单、标签阈值、DTW 配置 |
| `feature.py` | 33 特征提取（仅从 history 窗口） |
| `split.py` | 时间切分 + 泄漏自检 |
| `train.py` | 朴素基线 + LogisticRegression 训练评估 |

**验证说明**

- `infer`：执行正常窗口推理，从 B02 最新样本构造实时窗口，输出 MODEL_OUTPUT（predicted_attention、confidence、SHAP Top-5、DTW 值、33 特征值）
- `verify`：执行失败验证，包含参数错误、超时、空结果三个场景，输出完整 tool_call / execute / 原始返回报文证据

**MODEL_OUTPUT 结构**

```
predicted_attention: NORMAL / WATCH
confidence: float（概率值）
model_version: b06-YYYYMMDD-vN
shap.top5_contributions: [{feature, contribution}, ...]
dtw_temperature / dtw_humidity / dtw_lux: float
raw_features: {特征名: 值}（33 个）
errors: [异常记录]（容错收集）
```

---

## B07 — S1 稳态基线

B07 的 S1 阶段做一件事：**用「当前状态 + 最近历史 + 已保存的 B06 模型」回答一个固定问题，并且把整个过程存档，让事后能复算、能回溯。**

流程严格按序，判断与解释分离：

```
选稳定窗口 → 取数（当前状态 + 最近历史 + 时间锚点） → 加载 B06 模型推理
   → 结构化判断（纯程序，无 LLM） → LLM 解释（只解释，不判断） → 落 run 存档
```

**依赖**

- Python 3.12 + `requirements.txt`（需 `scikit-learn`、`numpy`、`joblib`、`openai`、`python-dotenv`）
- B06 模型产物必须已存在：`B06/artifacts/b06-20261020-v1/`（`b06-20261020-v1.joblib` + `.json` 清单）
- B02 数据库 `B02/greenmate.db`（原始样本，只读）
- 一个 OpenAI 兼容的 LLM 服务（复用 `B01/.env`；`import B01.llm` 会自动加载）
- 测试额外需要 `pytest`：`pip install pytest`

**运行命令**

```cmd
(venv) D:\GreenMate> python -m B07.s1 --question "分析当前番茄生长环境状态以及接下来值得关注的变化，并说明判断依据。"

(venv) D:\GreenMate> python -m B07 list
(venv) D:\GreenMate> python -m B07 explain run_001
(venv) D:\GreenMate> python -m B07.runs list
(venv) D:\GreenMate> python -m B07.runs explain run_001

(venv) D:\GreenMate> python -m pytest B07/tests -v
```

> `--question` 必须是 S1-4 定的固定问题，改写会被直接拒绝（退出码 2）：LLM 解释环节用的就是这个问题，换问法会让存档里的 `question` 与实际提问不一致，事后回溯失真。
> `python -m B07`（不带子命令）等价于 `python -m B07.s1`。

**预期输出**

```
[1/6] 选稳定窗口...
  候选稳定窗口 51 个（白天段 27 个）
  选中窗口：2026-10-21T13:30:00 → 2026-10-21T16:00:00
[2/6] 取数（当前状态 + 最近历史 + 时间锚点）...
  当前状态：32.8℃ / 56.7% / 39842.0 lux @ 2026-10-21T16:00:00
[3/6] 加载 B06 模型推理...
  predicted_attention=NORMAL，confidence=1.0，model_version=b06-20261020-v1
[4/6] 结构化判断（纯程序，无 LLM）...
  复算自检：✅ 逐字段一致
[5/6] LLM 解释（只解释，不判断）...
[6/6] 一致性校验（只记录，不修改）...

【run_id】run_005
【结构化判断五字段】
  ① current_environment / ② recent_trend / ③ predicted_attention
  ④ evidence_status / ⑤ limitations
【LLM 解释】
  ...
【阶段报告】D:\GreenMate\B07\artifacts\s1_report.md
```

**产物清单**

| 路径 | 内容 | 是否入库 |
|---|---|---|
| `B07/stability.py` | 稳定窗口扫描 + 透明稳定判定（5 条判据，阈值写在模块顶部） | ✅ |
| `B07/evidence.py` | 当前状态 + 最近历史 + 时间锚点，6 项校验不通过直接抛错 | ✅ |
| `B07/predict.py` | 只加载 B06 已存模型做一次预测（不重训、不重切数据） | ✅ |
| `B07/judgement.py` | 结构化判断五字段，可脱离 LLM 复算（`recompute_judgement`） | ✅ |
| `B07/explain.py` | LLM 解释 + 结论冲突 / 事实越界一致性校验 | ✅ |
| `B07/runs.py` | run 存档、index 索引、`explain_run` 回溯、`rebuild_index` | ✅ |
| `B07/s1.py` | S1 统一入口 + 阶段报告生成 | ✅ |
| `B07/s2.py` | S2 统一入口（渐进漂移，只做编排）+ 6 条自检 + 阶段报告 | ✅ |
| `B07/scenarios.py` | 场景注册表（S1 / S2） | ✅ |
| `B07/tests/` | S1 + S2 + S3 不变量测试（68 条，pytest） | ✅ |
| `B07/runs/index.json` | 索引：run_id → {created_at, scenario, question, verdict, model_version, status} | ❌ gitignore |
| `B07/runs/<run_id>/run.json` | 单次运行完整快照 | ❌ gitignore |
| `B07/artifacts/s1_report.md` | 阶段报告（run_id / 证据来源 / 判据 / 五字段 / coef Top5 / LLM 原文 / 回溯输出） | ✅ |
| `B07/artifacts/s2_report.md` | S2 阶段报告（S1/S2 并排对比 / 实验有效性证明 / 温度带约定 / 边界与局限） | ✅ |

> `B07/runs/*.json` 命中 `.gitignore` 的全局 `*.json` 规则，属于运行时产物，不入库；需要留档请自行备份该目录。

**留下什么资产**

- `B07/stability.py` — 稳定判据（温度波动 ≤2.0℃ / 湿度首尾 ≤3.0% / 湿度单步 ≤1.5% / 光照跳变 ≤15000 lux / 禁全程单调），判定结论带具体数值，可被人复述
- `B07/evidence.py` — 时间锚点取法与 6 项证据校验（点数、间隔、末点对齐、source_type 透传、hours_gap 计算）
- `B07/predict.py` — B06 模型的推理入口与 meta 一致性校验（不通过直接中止，不静默继续）
- `B07/judgement.py` — 结构化判断五字段规范，含 4 条从真实数据算出的 limitations
- `B07/explain.py` — LLM 解释环节与一致性校验（只记录 warning，不改判断）
- `B07/runs.py` — run 存档、索引、失败留痕、`explain_run` 回溯（复算而非复读）
- `B07/runs/` — 每次运行的完整快照与索引（本地留存）
- `B07/artifacts/s1_report.md` — 本阶段报告
- `B07/artifacts/s2_report.md` — S2 阶段报告（渐进漂移的对比证据）
- `B07/tests/` — 68 条不变量测试（S1 24 + S2 26 + S3 18），全部离线可跑（LLM 用假客户端、天气用假工具）

---

## B07 — S2 渐进漂移

S2 与 S1 **共用同一套流程、同一个固定问题、同一个 system prompt、同一个模型、同一套判据阈值**，
唯一不同的是**看哪一段历史**。所以 S1 与 S2 的输出可以直接横向比较。

> **S2 只改变历史过程，不新增任何模块。**
> 没接 B03 天气、没接 B04 知识库、没改 B01/B02/B05/B06 任何文件、没重训模型、没重切数据、没动 B02 数据库。
> 新增的只有 B07 内部三样东西：`B07/scenarios.py`（场景注册表）、
> `B07/s2.py`（S2 入口，只做编排，逻辑全部复用 `B07.runs.run_analysis()`）、
> 以及 `B07/stability.py` 里的选窗策略 `pick_drifting_window()`。
> 判据阈值、提示词文字、固定问题、模型产物一个字没改。

| | S1 稳态基线 | S2 渐进漂移 |
|---|---|---|
| `anchor_policy` | `stable_window` | `drifting_comparable_to_S1` |
| `expect_stability` | `True` | `False`（必须判为不稳定） |
| 固定问题 | 与 S2 同一个字符串对象，一字不改 | 同左 |
| 判据阈值 | 不变 | 不变 |
| 选中窗口 | 2026-10-21T13:30 → 16:00 | 2026-10-20T09:30 → 12:00 |
| 末点温度 | 32.8℃ | 32.8℃（与 S1 差 0.0℃，当前值可比） |
| ΔT | 0.8℃ | 7.5℃ |
| 稳定判定 | 稳定 | **不稳定（持续升温）** |
| B06 预测 | NORMAL | NORMAL（预期，见下） |

**S1 / S2 对照结论**

两次的**当前值几乎一样**（温度都是 32.8℃、湿度都是 56.7%，差 0.0℃），
**提示词也完全一样**（`system_prompt_sha256` = `aa1400cf8568`，
`question_sha256` = `d3cc394bfcbe`，两次完全相同），
**唯一的差别是喂进去的那 6 个历史点**：

- S1：33.9 → 34.1 → 34.0 → 33.9 → 33.3 → 32.8℃（在平台上缓降，判为**稳定**）
- S2：25.3 → 27.6 → 30.5 → 31.6 → 32.2 → 32.8℃（单调升温，判为**不稳定**）

**同一个 32.8℃，在 S1 是「稳态平台的最后一点」，在 S2 是「持续升温过程的最后一点」。**
GreenMate 判的是"当前值 + 历史过程"，不是当前值本身——这就是 S2 想证明的事。
完整证据见 `B07/artifacts/s2_report.md` 第 7~9 节。

**选窗策略 `drifting_comparable_to_S1`（实现在 `B07/stability.pick_drifting_window`）**

在全部 331 个连续 6 点窗口里，同时满足下面三条的才算候选，再取 ΔT 最大的：

- (a) 末点温度落在 S1 参考带 `[32.8, 33.6]℃`（S1 末点 32.8℃，容差 0.8℃，单侧带：末点须已达到 S1 水平）
- (b) 窗口内温度**全程单调上升**
- (c) `judge_stability` 判定为**不稳定**

选窗时数据先被投影成只含 `timestamp / temperature / humidity / lux` 的行，
`sim_scenario` 字段在这一步被物理丢弃——**想用也用不上**。
选窗后还会跑一次对抗性自检：把 `sim_scenario` 全改成 `"XXXXXXXX"` 重跑选窗，结果必须完全一样。
选中的窗口恰好跨了 `cold_snap → normal` 的场景边界，这正是"没偷看 sim_scenario"的证据。

**入口**

```cmd
(venv) D:\GreenMate> python -m B07.s2
(venv) D:\GreenMate> python -m B07 --scenario S2
(venv) D:\GreenMate> python -m B07 --scenario S1        # 等价于 python -m B07.s1
(venv) D:\GreenMate> python -m B07 --scenario S3        # 未知场景 → 报错退出（退出码 2）

(venv) D:\GreenMate> python -m B07.s2 --report-only     # 只按已有存档重建 s2_report.md（不新增 run）
(venv) D:\GreenMate> python B07\check_s2_experiment.py --s1 run_012 --s2 run_015
(venv) D:\GreenMate> python -m B07.runs rebuild-index   # 按实际存在的存档重建 index.json
(venv) D:\GreenMate> python -m B07.runs explain run_014
(venv) D:\GreenMate> python -m B07 list
(venv) D:\GreenMate> python -m pytest B07/tests -v
```

> `--question` 同样必须是固定问题，改写直接被拒（退出码 2）。
> `--scenario` 只在跑全流程时生效；`list / explain / run` 仍是 B07.runs 的子命令。

**提示词指纹**

`llm_meta` 里新增两个字段，S1 / S2 存档可以直接比对"是不是同一套提示词、同一个问题"：

| 字段 | 值（当前） |
|---|---|
| `system_prompt_sha256` | `aa1400cf8568`（= `sha256(SYSTEM_PROMPT)[:12]`） |
| `question_sha256` | `d3cc394bfcbe`（= `sha256(FIXED_QUESTION)[:12]`） |

SYSTEM_PROMPT 文字本身一个字没动。

**实际输出（run_014）**

```
选中窗口：2026-10-20T09:30:00 → 2026-10-20T12:00:00
  25.3℃/68.8%/44222 → 27.6℃/66.1%/50200 → 30.5℃/62.3%/53208
  → 31.6℃/59.4%/55877 → 32.2℃/57.9%/58106 → 32.8℃/56.7%/59594

逐条判据
  ❌ 温度最大波动     实测 7.5  阈值 2.0     超出 5.5
  ❌ 湿度首尾总变化   实测 -12.1  阈值 3.0    超出 9.1
  ❌ 湿度单步最大变化 实测 3.8  阈值 1.5     超出 2.3
  ✅ 光照单步最大跳变 实测 5978  阈值 15000
  ❌ 湿度无持续单向趋势  全程单调下降

稳定性结论句
  近 3 小时温度 25.3→32.8℃ 单调上升，温度波动 7.5℃ 超过阈值 2.0℃（超出 5.5）；
  湿度首尾变化 -12.1% 超过阈值 3.0%（超出 9.1）；湿度单步最大变化 3.8% 超过阈值 1.5%（超出 2.3）；
  湿度全程单调下降（判据禁止全程单调） → 判定为不稳定（持续升温）
```

结论句必须写清"不稳定"、超了哪个阈值、超了多少——否则 LLM 解释不会提到升温，S2 就白做了。
S1 的结论文本**逐字未变**（富化措辞只在 S2 开启）。

**实验有效性自检（S2 每次运行自动打印 6 条）**

| # | 检查项 | 实测 |
|---|---|---|
| 1 | 本次 LLM 的问题字符串与 S1 存档完全相同 | S2 = S1(run_012) = `分析当前番茄生长环境状态以及接下来值得关注的变化，并说明判断依据。`；旧存档 run_001 也逐字相同 |
| 2 | `system_prompt_sha256` 与 S1 存档相同 | 本次 `aa1400cf8568` = run_012(S1) = 现场实算 `sha256(SYSTEM_PROMPT)[:12]`；run_001 是旧存档，没有指纹字段 |
| 3 | S2 与 S1 的当前状态温度差 ≤ 0.8℃ | 32.8℃ vs 32.8℃，差 **0.0℃** |
| 4 | S2 稳定判定 `passed == False` | `passed=False` |
| 5 | 存档 `scenario == "S2"`，index.json 能分别列出 | S1=`['run_012']`，S2=`['run_014']` |
| 6 | ★对抗性自检：污染 `sim_scenario` 后选窗不变 | 干净 `09:30→12:00 / temp_range 7.5` = 污染后 `09:30→12:00 / temp_range 7.5` |

**预期行为（不是 bug）**

- B06 仍预测 **NORMAL**：B06 判断的是"未来 1-2 小时是否越界"（阈值 TEMP_MAX=40℃），32.8℃ 没越界。
  不为制造差异去改模型输出、改阈值或重训。
- S2 的 `hours_gap = 37.5` 小时（锚点 2026-10-20 12:00 → 库末点 2026-10-22 01:30），
  超过 `config.GAP_WARNING_HOURS = 24` → **必然多出一条 gap 警告**，输出里单独归类为"时间锚点 gap 警告（预期）"。
- B06 的 `recall ≈ 0.45`（测试集 accuracy=0.745 / precision=0.591 / recall=0.448 / f1=0.510），
  对异常窗口存在明显漏报 → **本次 S2 输出 NORMAL 不能当成"模型在异常场景下也能正确判断"的证据**，
  把趋势识别出来的是结构化判断的 `recent_trend`，不是模型输出。

**末点温度带的约定（实验设计决策）**

S2 选窗条件 (a) 采用**单侧带 `[32.8, 33.6]℃`**：S2 是升温过程，末点天然不低于 S1 基准。
若用对称带 `[32.0, 33.6]`，会选到 2026-10-20 09:00→11:30（末点 32.2℃、ΔT=8.8），
该窗口末点低于 S1 基准、实为"从更冷处升上来"，"当前值相近"的对照会变弱。
判据阈值侧一个字没动，变化只发生在"挑哪一段历史"这一步。详见 `s2_report.md` 第 2.1 节。

**产物清单**

| 路径 | 内容 | 是否入库 |
|---|---|---|
| `B07/scenarios.py` | 场景注册表（S1 / S2）：哪个场景用哪个 anchor_policy | ✅ |
| `B07/s2.py` | S2 统一入口（只做编排）+ 6 条自检 + 阶段报告 + `--report-only` | ✅ |
| `B07/stability.py` | 新增 `pick_drifting_window()`、`project_rows()`、`judge_stability(..., rich_failure=True)` | ✅ |
| `B07/runs.py` | 存档 / 索引里的 `scenario` 字段、`scenario_label()`、回溯显示场景、`rebuild_index()` | ✅ |
| `B07/explain.py` | `llm_meta` 的 `system_prompt_sha256` / `question_sha256` 指纹 | ✅ |
| `B07/__main__.py` | `--scenario S1 / S2` 分发，未知场景报错 | ✅ |
| `B07/artifacts/s2_report.md` | S2 阶段报告（S1/S2 并排对比、实验有效性证明、温度带约定、边界与局限） | ✅ |
| `B07/check_s2_experiment.py` | S2 实验有效性校验脚本（只读两份存档，9 条自检 + 并排对比，不产生新 run） | ✅ |
| `B07/tests/test_s2_invariants.py` | S2 不变量测试（26 条，含对抗性检验、指纹、S1/S2 存档对照、目录隔离） | ✅ |

---

## B07 — S3 天气证据接入层（B03 未来外部天气）

S3 分三步走完：**S3-0 接入**（把 B03 的公开天气数据接进来）→ **S3-1 判定**（把外部信号接进结构化判断）→
**S3-2/3 实验与报告**（单变量对照实验 + 可审计报告）。

> **S3 的硬边界**：不接 B04 知识库、不接摄像头、不接新智能体，
> 不改 B01 / B02 / B06，**不改 LLM 提示词与固定问题**（指纹 `aa1400cf8568` / `d3cc394bfcbe` 全程未变）。

**完整报告：[`B07/artifacts/s3_report.md`](B07/artifacts/s3_report.md)**
（由 `python -m B07.s3_report` 从实测存档自动生成，报告里每个数字都可复核）

---

### S3-0 接入层

`B07/weather.py` 通过 **B05 的稳定工具接口** `B05.test_functions.get_weather_forecast()` 取天气，
**不绕过 B05 直接调 B03，也不重写天气请求、不新增任何网络调用**。
为此只改了 B05 一处：给 `get_weather_forecast` 加了可选参数 `forecast_window: str = "now"`，
默认 `"now"` → 既有调用方行为完全不变，非 `"now"` 时透传给 B03 的 `get_weather_evidence(..., forecast_window=...)`。
请求参数拼装与 ok / unavailable / error 三态错误处理一个字没动。

**接口**

```python
from B07.weather import build_weather_evidence
ev = build_weather_evidence(analysis_time)   # 默认 河北固安 / 6h
```

默认参数为什么是"河北固安 / 6h"：

> 默认地点用河北固安而非山东寿光：实测当前寿光外部条件平稳
> （降水概率 0%、晴），无法体现"外部条件将有明显变化"；
> 固安当前降水概率 21%、+6h 升至 70%，外部信号有区分度。
> 注意：真实天气的数值随真实时间变化，地点与窗口是教学参数，
> 选点依据是"能让外部条件信号有区分度"，不是为了制造某个结论；
> 若当天实测外部条件平稳，external_signal 会是 external_stable，
> 这同样是合法结果。

**三条硬规矩**

| 规矩 | 实现 | 违反时的表现 |
|---|---|---|
| 天气必须真的在 analysis_time 之后 | `future_ok = forecast_time > analysis_time` | `future_ok=False` + warning，**data 仍然保留**（不静默丢弃这份证据） |
| 拿不到就明说拿不到 | `status != "ok"` → `available=False`、`error` 原样带出、`data=None` | **绝不用静态文案伪装成成功的天气** |
| 天气与 B02 不在同一时间基准 | 恒定一条 warning，写明 B02 模拟时间轴的实际范围 | 只作"外部条件方向参考"，不做时刻级跨源对齐 |

`available` 与 `future_ok` 是两个字段，不混着骗人：
`available` 只表示"取到了"，`future_ok` 才表示"能当未来天气用"。

**B03 的两处修复（S3-0 修订）**

原先 `get_weather_evidence` 有两个缺陷，导致 `forecast_window` 形同虚设：

1. **取值只读 `current`**：`hourly` 预报数组请求了却从未被读取 →
   `forecast_window="3h"` 取到的仍是当前实况。
2. **`forecast_days` 算不够**：原式 `max(1, (hour_offset // 24) + 1)` 只按偏移量算天数，
   跨夜时（现在 22 点、+6h → 目标 28 点）hourly 覆盖不到目标小时。

修法（只改这两处）：

- `forecast_days` 按"当前小时 + hour_offset"计算：`max(2, hours_needed // 24 + 1)`（至少 2 天留冗余）。
  `current["time"][11:13]` 是响应里的值、请求前拿不到，
  而 Open-Meteo 的 `current.time` 用的是请求里的 `timezone=Asia/Shanghai`，
  所以请求前用同一时区的本地钟点小时，与 `current["time"][11:13]` 等价。
- 取值逻辑：`hour_offset > 0` 时在 `hourly["time"]` 里按前 13 位（`YYYY-MM-DDTHH`）
  定位当前小时的索引 `idx`，取 `j = idx + hour_offset` 那一小时的值，
  `timestamp` 用 `hourly["time"][j]`——**这才是真正的 forecast_time**。
  `j` 越界或 `hourly` 缺失时**抛明确异常，不静默回退到 current**
  （回退等于让 `forecast_window` 变回摆设）。
- `hour_offset == 0`（默认 `"now"`）走原路径，**行为完全不变**（见下方新旧对照）。

**新旧行为对照（`forecast_window="now"`，旧版 = `git show HEAD:B03/weather_service.py`）**

| 地点 | 逐字段完全相同 | timestamp | T | precip | cloud | 描述 |
|---|---|---|---|---|---|---|
| 山东寿光 | ✅ True | 2026-10-24T22:30 | 13.8 | 0.0 | 0.0 | 晴朗 |
| 河北固安 | ✅ True | 2026-10-24T22:30 | 13.4 | 21.0 | 100.0 | 阴天 |
| 北京大兴 | ✅ True | 2026-10-24T22:30 | 13.3 | 34.0 | 100.0 | 阴天 |

含 `location / forecast_window / timestamp / temperature / humidity /
precipitation_probability / cloud_cover / solar_radiation / weather_code /
weather_description / provider` 在内全部字段逐字段相等（`retrieved_at` 为取数时刻，单独比对时区后缀）。

---

### S3-1 判定层：相对差分为主，绝对阈值只兜底

外部信号的判定在 `B07/weather.py` 的 `judge_external_points()` 里，**纯函数、不联网**，
在线取数与"从存档重读后复算"用的是同一份判据。

**主判定：相对基线差分**

```
ΔPP = forecast.precipitation_probability - baseline.precipitation_probability
ΔPP ≥ +30 pp → external_deteriorating
ΔPP ≤ -30 pp → external_improving
其余          → external_stable
```

**兜底（仅当基线点 `now` 取不到时）**：`judge_method = "absolute_fallback"`
（预报降水概率 ≥ 60 → 恶化；≤ 10 且云量 ≤ 20 → 好转；其余 → 平稳）。

**为什么不用绝对阈值当主判定**——用同一份实现对同一组点各判一次
（`B07/artifacts/s3_calibration.json` 是校准数据文件，代码里不写死任何天气数值）：

| 组 | 降水概率 | 相对差分 | 绝对阈值 | 结论 |
|---|---|---|---|---|
| 寿光（晴天，S3-0 实测校准） | 0.0% → 0.0% | `external_stable` | `external_improving` | ⚠️ **不一致** |
| 构造·5% → 55% | 5.0% → 55.0% | `external_deteriorating` | `external_stable` | ⚠️ **不一致** |
| 固安（S3-0 实测校准） | 21.0% → 70.0% | `external_deteriorating` | `external_deteriorating` | ✅ 一致 |
| 大兴（S3-0 实测校准） | 34.0% → 96.0% | `external_deteriorating` | `external_deteriorating` | ✅ 一致 |

绝对阈值**只看预报点的绝对值、没有基线可比**，于是 `(10%, 60%)` 开区间里的任何值一律判"平稳"，
`≤10%` 且云量 `≤20%` 一律判"好转"——**"本来就晴"被误判成"在好转"**。所以主判定必须用相对差分。

**接进结构化判断的方式：`external_conditions` 是第 6 个字段，只追加、不替换**

- `external_conditions is None` → 结构化判断**严格是原来的 5 个字段**，
  字段名、取值、顺序全都不变（既有 S1 / S2 的不变量不受影响）。
- `external_conditions` 非 None → 5 + 1 = 6 个字段，**前 5 个逐字段完全相同**。
  这个不变量由 `B07/tests/test_s3_invariants.py` 断言锁住。

**外加一道硬门槛**：预报时刻必须真的晚于 `analysis_time`，
否则**不产生任何判定，也不返回 `external_stable`**——拿一份不在未来的预报去说"稳定"是误导。

---

### S3-2 单变量对照实验

`python -m B07.s3` 跑一组对照：**同一个锚点**（`2026-10-21T16:00:00`）、
同一套提示词、同一个问题、同一个 B06 模型、同一段 B02 历史、同一套阈值，
**唯一的变量是"发给 LLM 的 state 里有没有 ⑥ 外部天气条件块"**。

| | Run A（`S3_without`） | Run B（`S3_with`） |
|---|---|---|
| 结构化判断字段数 | 5 | 6（`external_conditions` 为追加） |
| 前 5 个字段 | — | **逐字段与 A 相同** |
| LLM 上下文 | 5 块 / 2599 字 | 6 块 / 3212 字，**前 2599 字与 A 逐字节相同**，B == A + `"\n\n"` + ⑥ 块 |
| 提示词 / 问题指纹 | `aa1400cf8568` / `d3cc394bfcbe` | 同上，一字未改 |
| 外部判定 | 无 | `external_deteriorating`（相对差分，Δ52.0 pp） |
| **合成关注等级** | `internal_only` | **`watch_external`** |

**失败必须显式暴露**：天气服务不可用时，整次分析 `status` 仍是 `success`，
外部条件被显式标成 `external_unavailable` 并带 warning，
LLM 上下文里写明"外部天气不可用、不要编造天气"，
实测降级回答里**零天气断言、零外部数值**。

**run_016 / run_017 是失败的尝试，保留在存档里不删**：Run B 收到了 ⑥ 块，
回答却写"当前判断未接入真实天气与农艺知识库"——根因是 ⑤ 限制条件里的
"本阶段未接入 B03 天气数据"与 ⑥ 自相矛盾，模型挑了更保守的那一条。
修法是把 `B07/judgement.py` 的 `no_weather_no_kb` 文案改成两次都成立的中性表述。
**教训**：接入外部证据时，必须同步清理描述"未接入该证据"的旧文案。

---

### S3-3 合成关注等级 `combined_attention`（程序规则，不经过 LLM）

规则写死在 `B07/judgement.py`：

```
内部 predicted_attention == "WATCH"                  → watch_internal
内部 NORMAL 且外部 signal == "external_deteriorating" → watch_external
内部 NORMAL 且外部 signal ∈ {stable, improving, unknown} → internal_only
```

- **不是**结构化判断的第 7 个字段（那会破坏 5/6 字段的不变量），
  只是存档里的一个**独立字段** `combined_attention`。
- **不进 LLM 上下文**——这样"上下文只差 ⑥ 块"这个可复核的不变量才成立。
- **可脱离 LLM 复算**：`python -m B07.runs explain <run_id>` 只用 `run.json`
  就能把内部判定、外部判定、稳定判据、`combined_attention` 全部重新推导一遍并与存档值对照。

**但要说清楚**：`combined_attention` 是确定的，**模型会不会引用 ⑥ 块不是确定的**。
在全部 16 次 `S3_with` 运行里，引用率是 **14/16**；但**按 `llm_context` 分组看**
（同一个 sha = 喂给 LLM 的上下文逐字节相同），组内差异只能来自 LLM 采样：

| llm_context sha | 次数 | 引用 | 外部 Δpp |
|---|---|---|---|
| `125298fb60b7` | 12 | 12/12 | 43.0 pp |
| `83ccc0be47d3` | 2 | **1/2** | 52.0 pp |
| `d1cf9e568eaf` | 1 | 0/1 | 54.0 pp |
| `e33083033e8d` | 1 | 1/1 | 56.0 pp |

`run_021` 与 `run_023` 的 `llm_context` **逐字节相同**，
一次引用了外部天气、一次没引用——这是"可测量的解释层波动"的直接证据。
所以"外部证据接入后模型会讨论外部天气"这句话目前**不成立**，
只能说"模型**有时**会讨论外部天气"，而且"有时"的比例**随 ⑥ 块内容变化**。

**这正是 B07「判断与解释分离」架构的价值**：判断层只由程序规则决定且可复算，
解释层的波动被**显式度量**（引用率 N/M）并**隔离**在解释环节——
**完成线不随 LLM 措辞波动**。详见报告 §3.7（同状态并排样本）、
§3.8（分组引用率）、§3.9（三种来源的区分）、§3.10（架构论点）与 §8 L13。

---

### 已知边界（不是缺陷，是 B03 现状）

- `_WINDOW_TO_HOURS.get(w, 0)` 对未知窗口**静默降级为 now**；
  层里对不在支持列表内的窗口单独记一条 warning。
- 天气是 PUBLIC 真实数据，B02 是 SIMULATED 模拟数据，**时间基准不同**，只作方向参考。
- 真实天气数值随真实时间变化，**不得把任何实测值硬编码进代码**。
- **全部实测点都在夜间**（22:00 → 05:00），`solar_radiation` 恒为 0，
  高温分支（≥35℃）**完全没被实测覆盖过** → 需要白天 / 夏季再验。
- 外部判定**只用降水概率一个维度**；温度、云量只作描述，湿度（实测 91%）尚未参与判定。
- 外部取的是窗口末时刻的**单点值**，不是窗口极值，中途更高/更低的值可能被漏掉。
- `MAX_ANSWER_CHARS = 250` 目前只是提示词里的约束说明常量，**代码不做强制截断**；
  实测回答经常超（Run A 318 字 / Run B 268 字），
  而硬截断会把外部引用句拦腰截断（`run_021` 的引用句在第 244–259 字）。

### 产物清单

| 路径 | 内容 | 是否入库 |
|---|---|---|
| `B07/weather.py` | S3 天气证据接入层 + 外部判定（`build_weather_evidence` / `build_external_context` / `judge_external_points` + CLI 自检），默认 河北固安 / 6h | ✅ |
| `B07/judgement.py` | 结构化判断的第 6 字段 `external_conditions` + 合成关注等级 `combined_attention` | ✅ |
| `B07/s3.py` | S3-2 对照实验（真实组 / 离线组 / 降级验证 / 两种判据对照） | ✅ |
| `B07/s3_report.py` | 从实测存档生成 `s3_report.md` | ✅ |
| `B07/s3_citation_probe.py` | 引用率探针：冻结 ⑥ 块重复跑 N 次，只测 LLM 采样波动（失败也计入分母） | ✅ |
| `B07/s3_citation_table.py` | 全部 S3_with 存档的统一判定表（mentions / claims / cited 三级） | ✅ |
| `B07/verify_s3.py` | **S3 阶段验收自检**（一条命令跑完 14 项检查，全部离线、不调 LLM） | ✅ |
| `B07/artifacts/s3_report.md` | **S3 阶段报告**（0–10 节 + L1–L13 限制清单） | ✅ |
| `B07/artifacts/s3_raw_result.json` | 报告的原始数据（`python -m B07.s3` 落盘） | ✅ |
| `B07/artifacts/s3_offline_result.json` | 离线组重跑结果（判定器扩词后重跑，报告以此为准） | ✅ |
| `B07/artifacts/s3_citation_stats.json` | 引用率探针的记账（分母含失败） | ✅ |
| `B07/artifacts/s3_citation_table.json` | 全部 S3_with 存档的判定明细 | ✅ |
| `B07/artifacts/s3_frozen_ext.json` | 冻结的 ⑥ 块（保证"同一状态"跨调用成立） | ✅ |
| `B07/artifacts/s3_calibration.json` | 判据校准点（数据文件，不是代码常量） | ✅ |
| `B03/weather_service.py` | 修 `forecast_days` 跨夜覆盖 + 改用 `hourly` 取目标小时预报（越界显式抛错） | ✅ |
| `B05/test_functions.py` | `get_weather_forecast` 增加可选参数 `forecast_window="now"`（其余逻辑未动） | ✅ |
| `B07/tests/test_weather_invariants.py` | S3 不变量测试（离线，用假天气工具，不联网） | ✅ |
| `B07/tests/test_s3_invariants.py` | S3-1/2/3 不变量测试（离线） | ✅ |

### 复现命令

```bash
./venv/Scripts/python.exe -m B07.s3          # 跑对照实验，落盘 s3_raw_result.json
./venv/Scripts/python.exe -m B07.s3_report   # 由原始结果生成 s3_report.md
./venv/Scripts/python.exe -m B07.runs explain run_023   # 只用存档重算一遍
./venv/Scripts/python.exe -m pytest B07/tests -q        # 离线不变量测试

# 引用率探针（同一 anchor / 同一状态，只测采样波动；失败也计入分母）
./venv/Scripts/python.exe -c "from B07.s3_citation_probe import main; main(8, 4, append=True)"
./venv/Scripts/python.exe -m B07.s3_citation_table       # 全部 S3_with 存档的判定表
```

### 验收自检（一条命令，全部离线）

```bash
./venv/Scripts/python.exe -m B07.verify_s3
```

跑 14 项检查，**不联网、不调 LLM**（LLM 额度用尽时也能跑）：

| 检查 | 内容 |
|---|---|
| C1 | `B07/tests` 全过 |
| C2 | 全部 S3_with 存档可被当前判定器重判，且与落盘表格一致 |
| C3 | 存在"同一 `llm_context` 既有引用又有未引用"的分组（波动证据） |
| C4 | `combined_attention` 可脱离 LLM 从存档复算一致 |
| C5 | 报告可重新生成，且含 §3.7/§3.8/§3.9/§3.10/L13 |
| C6 | 55% 那条只能以「构造用例」出现，不得混进实测表 |
| C7 | 存档目录与 `index.json` 一致；`run_016`/`run_017` 仍在且带说明 |
