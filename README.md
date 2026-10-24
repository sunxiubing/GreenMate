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
├── B07/                   # S1 稳态基线：选窗 → 取数 → 推理 → 判断 → 解释 → 存档
│   ├── __init__.py        # 包声明（让 python -m B07.runs 能跑）
│   ├── __main__.py        # 入口分发：默认 S1 全流程，list/explain 透传给 runs
│   ├── s1.py              # S1 统一入口 + 阶段报告生成
│   ├── config.py          # 位置 / 窗口点数 / 采样间隔 / gap 阈值
│   ├── stability.py       # 稳定窗口扫描 + 透明稳定判定规则
│   ├── evidence.py        # 当前状态 + 最近历史 + 时间锚点（含校验）
│   ├── predict.py         # 加载 B06 已存模型做一次预测
│   ├── judgement.py       # 结构化判断五字段（纯程序，无 LLM）
│   ├── explain.py         # LLM 解释 + 一致性校验（只解释，不判断）
│   ├── runs.py            # run 存档 + index 索引 + explain_run 回溯
│   ├── tests/             # S1 不变量测试（pytest）
│   │   ├── conftest.py    # 路径注入 + 假 LLM 客户端
│   │   └── test_s1_invariants.py
│   ├── runs/              # 运行时生成的存档（gitignore *.json）
│   │   ├── index.json     # 索引：run_id → 摘要
│   │   └── run_XXX/run.json
│   └── artifacts/         # 阶段报告
│       └── s1_report.md
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
| `B07/runs.py` | run 存档、index 索引、`explain_run` 回溯 | ✅ |
| `B07/s1.py` | S1 统一入口 + 阶段报告生成 | ✅ |
| `B07/tests/` | S1 不变量测试（22 条，pytest） | ✅ |
| `B07/runs/index.json` | 索引：run_id → {created_at, question, verdict, model_version, status} | ❌ gitignore |
| `B07/runs/<run_id>/run.json` | 单次运行完整快照 | ❌ gitignore |
| `B07/artifacts/s1_report.md` | 阶段报告（run_id / 证据来源 / 判据 / 五字段 / coef Top5 / LLM 原文 / 回溯输出） | ✅ |

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
- `B07/tests/` — 22 条不变量测试，全部离线可跑（LLM 用假客户端）

