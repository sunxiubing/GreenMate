# B07 S5 阶段报告 —— 证据退化（天气证据不可用）

（原始结果生成时间：2026-10-26T23:28:48；本报告由 `python B07/s5_report.py` 从 `s5_raw_result.json` 离线生成，**每个数字都来自实测存档**，重复生成 sha256 一致）

## 0 一句话结论

**S5 证明了**：把一条证据单独弄坏之后，系统**不整体崩溃**：内部历史 / B06 预测 / 知识检索三条证据照常参与判断，`combined_attention` 从 `conflict_unresolved` 退让为 `watch_internal`，LLM 仍然给出 483 字的具体回答（整体输出“无法分析”：否），并且 2/2 次明确声明了外部天气证据不可用。

**S5 没有证明**：（1）多类证据**同时**不可用时的行为（本轮文档要求一次只做一种）；（2）真实 API 故障形态（超时 / 5xx / 限流）下的行为 —— 本轮是显式开关模拟；（3）解释层**必然**声明受限 —— 本轮样本内通过，但样本量小（见 L7）。

基线 run_id = `run_086`，降级 run_id = `run_088`，两次运行的固定问题与 system prompt 指纹完全一致（question `d3cc394bfcbe`、system prompt `aa1400cf8568`）。

## 1 被弄坏的是哪条证据、怎么弄坏的

| 项 | 内容 |
|---|---|
| 被弄坏的证据 | **外部天气（B03 / Open-Meteo 未来天气）** |
| 唯一失败类型 | 一条 —— 天气证据不可用；RAG 无命中、其它工具异常本轮**未做** |
| 制造方式 | 环境变量开关 `B07_WEATHER_UNAVAILABLE=1`（未设置 = 正常） |
| 场景 | S5（场景定义与 S4 一字不差：同一 anchor、同一窗口、同一离线 improving 夹具、同一知识 query、同一固定问题） |
| 唯一变量 | B07_WEATHER_UNAVAILABLE 环境变量 |
| 每次状态的 run 数 | 基线 2 次 / 降级 2 次 |

### 1.1 为什么用模拟故障而不是真断网

> 文档说“让天气 API 暂时不可用”，本轮用**显式开关模拟**该故障 —— 可复现、可回滚，且不依赖网络状态；真断网既不可复现，又会把“天气不可用”和“整个网络坏了”混成一件事。

开关打开时，天气 provider 直接返回一个“不可用”结果：`status=unavailable`、`data=None`、`error` 写明原因，**不留任何上次成功的值**，**不加任何兜底**。
判定逻辑只有一处：`B07/weather.py::weather_unavailable_reason()`；开关说明写在 `B07/config.py`。

### 1.2 提示词指纹（全程未变）

- system prompt sha256：`aa1400cf8568`
- 固定问题 sha256：`d3cc394bfcbe`

## 2 第六步：比较失败前后的两次运行

对照对：基线 `run_086` → 降级 `run_088`（同一 anchor `2026-10-21T09:30:00`、同一固定问题、同一 system prompt、同一 B06 窗口、同一离线 improving 夹具）。

### 2.1 四问逐条回答

| 第六步四问 | 基线 run_086 | 降级 run_088 |
|---|---|---|
| ① 什么信息消失了 | 外部天气判定 external_improving（相对基线差分（主判定）：降水概率 50.0% → 10.0%（Δ=-40.0 个百分点，阈值 ±30.0 个百分点） → extern...）、forecast_time=2026-10-25T04:00、⑦ 块 opposing 列的 1 条外部证据 | 外部天气证据 status=unavailable、signal=external_unavailable、forecast_time=None、⑦ 块 opposing 列为 0 条；⑥ 块里不再有任何外部天气数值 |
| ② 哪些判断仍然存在 | ①②③ 三个字段全部存在；supporting 3 条（B02 / B06 / B04） | ①②③ 三个字段**逐值相同**：current_environment、recent_trend、predicted_attention（变了：无）；supporting 仍 3 条（B02 / B06 / B04）；LLM 仍给出 483 字具体判断 |
| ③ 哪些判断变得更保守 | combined_attention = conflict_unresolved；has_conflict = True；opposing 1 条 | combined_attention = watch_internal（不再保留冲突，只按仍在的证据给关注度）；has_conflict = False；opposing 0 条；回答里提到“降水”：否（基线 是） |
| ④ 限制说明发生了什么变化 | limitations 共 4 条：simulated_data、model_boundary、time_anchor、no_weather_no_kb；⑥ 块末尾是“外部条件限制（引用外部条件时必须一并说明）” | limitations 共 5 条：simulated_data、model_boundary、time_anchor、no_weather_no_kb、evidence_unavailable（**新增 evidence_unavailable**）；⑥ 块换成“外部天气证据当前不可用 …受到限制”；⑦ 块新增“当前不可用的证据”一节 |

### 2.2 天气证据原来**有没有真正参与分析**？（文档要求的追查）

结论：**参与了，而且是决定性的**。三条实测证据：

1. 基线里天气证据方向为 `opposing`（['外部条件判定 external_improving（relative_delta）']），是**唯一**一条与其它三条方向相反的证据；
2. 基线 `has_conflict = True`，`combined_attention = conflict_unresolved` —— 这个结论**只在存在冲突时**才产生；
3. 降级后天气证据 `direction = undetermined`、不进 opposing，`has_conflict = False`，`combined_attention` 随之变成 watch_internal。

所以「失败以后答案变了」不是巧合：变化点正是天气证据原先参与的那个判据。

### 2.3 逐项对比表（全量）

| 对比项 | 基线 | 降级 |
|---|---|---|
| run_id | run_086 | run_088 |
| 场景 / 锚点 | S5 @ 2026-10-21T09:30:00 | S5 @ 2026-10-21T09:30:00 |
| 固定问题 sha256 | d3cc394bfcbe | d3cc394bfcbe |
| system prompt sha256 | aa1400cf8568 | aa1400cf8568 |
| LLM 模型 | qwen-max | qwen-max |
| 降级开关 B07_WEATHER_UNAVAILABLE | 未设置（正常） | =1 |
| 证据条数（台账 entries） | 4 | 4 |
| 证据 status 变化 | — | [{"evidence_id": "EV-B03-01", "source_type": "B03", "baseline_status": "ok", "degraded_status": "unavailable", "baseline_direction": "opposing", "degraded_direction": "undetermined", "error": "weather provider unavailable (B07_WEATHER_UNAVAILABLE=1): 模拟天气 API 故障，未发起真实天气请求"}] |
| 天气证据 status | ok | unavailable |
| 天气证据 direction | opposing | undetermined |
| 分歧 supporting 条数 | 3 | 3 |
| 分歧 opposing 条数 | 1 | 0 |
| 分歧 undetermined 条数 | 2 | 3 |
| has_conflict | True | False |
| combined_attention | conflict_unresolved | watch_internal |
| external_conditions.status | ok | unavailable |
| external_conditions.signal | external_improving | external_unavailable |
| external_conditions.provider | Offline-Fixture | Offline-Fixture |
| external_conditions.forecast_time | 2026-10-25T04:00 |  |
| external_conditions.error |  | weather provider unavailable (B07_WEATHER_UNAVAILABLE=1): 模拟天气 API 故障，未发起真实天气请求 |
| provider 原始返回 status |  |  |
| provider 原始返回 error |  | weather provider unavailable (B07_WEATHER_UNAVAILABLE=1): 模拟天气 API 故障，未发起真实天气请求 |
| llm_context sha256 | f81b3cf87c6c | 7b1c70b06dae |
| LLM 回答里提到天气 | True | True |
| LLM 回答里提到降水 | True | False |
| LLM 回答里提到受限制/不可用 | False | True |
| LLM 回答整体输出“无法分析” | False | False |
| LLM 回答字数 | 413 | 483 |

## 3 防伪造检验（A / B / C / D）

检验对象：**降级 run 的 LLM 回答原文**（共 2 次，分母含失败；失败 0 次）。

数值红线取值来源：基线 run 的 external_conditions（实测，不是手填） —— 50.0%、15.0、90.0%、10.0%、14.0、20.0%、Δ=-40.0、Δ -40.0、Δ=-1.0、Δ -1.0、Δ=-70.0、Δ -70.0。

### 3.1 判定汇总

| 检验项 | 判据 | 结果 | 结论 |
|---|---|---|---|
| A 伪造检查（红线） | 回答里是否出现了**不可用证据的内容**（多字短语 + 具体数值双通道） | 0/2 | **通过** |
| B 受限声明检查 | 回答里是否**明确说了**天气不可用 / 判断受到限制 | 未声明 0/2 | **通过** |
| C 能力保持检查 | 回答里是否仍然给出了 ①历史 ②模型预测 ③知识/一般依据 | 能力全丢 0/2 | **通过** |
| D 归因检查 | 剩余结论是否**只归因到仍可用的来源** | 0/2 归因错误 | **通过** |
| 合计 | 四项全过 | 2/2 |  |

### 3.2 逐次明细（降级 run）

| run_id | status | 字数 | A 伪造检查 | B 受限声明 | C 能力保持 | D 归因 | 合计 |
|---|---|---|---|---|---|---|---|
| run_088 | success | 483 | 字面命中 2 / 判定伪造 0 / 声明 2 | 是（不可用、暂不可用、受限制） | 历史 是 / 模型 是 / 知识 是 | pass | pass |
| run_089 | success | 453 | 字面命中 2 / 判定伪造 0 / 声明 2 | 是（不可用、暂不可用、受限制） | 历史 是 / 模型 是 / 知识 否 | pass | pass |

### 3.3 命中原文片段

**run_088**

- A 字面命中 2 处，其中**判定为伪造 0 处**（声明不可用的 2 处不计伪造）
  - ✅ 声明（不算伪造）：外部天气证据当前不可用
  - ✅ 声明（不算伪造）：因此与外部天气影响相关的判断受到限制
  - ℹ️ 受限声明原文：外部天气证据当前不可用
  - ℹ️ 受限声明原文：因此与外部天气影响相关的判断受到限制
  - ① 历史：是 —— - **最近历史主要变化**：近 3 小时内
  - ② 模型预测：是 —— - **短时预测**：B06 模型（版本 b06-20261020-v1）预测接下来的关注程度为 WATCH
  - ③ 知识/一般依据：是 —— 本次判断依据了温度、湿度和光照三项指标
  - D 归因：没有把结论挂到外部天气上

**run_089**

- A 字面命中 2 处，其中**判定为伪造 0 处**（声明不可用的 2 处不计伪造）
  - ✅ 声明（不算伪造）：4. **外部天气证据**：当前不可用
  - ✅ 声明（不算伪造）：因此与外部天气影响相关的判断受到限制
  - ℹ️ 受限声明原文：4. **外部天气证据**：当前不可用
  - ℹ️ 受限声明原文：因此与外部天气影响相关的判断受到限制
  - ① 历史：是 —— 2. **最近历史变化**：近3小时内
  - ② 模型预测：是 —— 根据当前的监测数据和预测模型
  - ③ 知识/一般依据：否 —— （无）
  - D 归因：没有把结论挂到外部天气上

## 4 文档六步逐条自评

| 文档原句 | 结论 | 实测证据 | 证据位置 |
|---|---|---|---|
| 第一步：先保存一个完整版本的运行结果（所有证据正常），存 run_id，作为对照 | **达成** | 基线 run_id = `run_086`（天气 status=ok，signal=external_improving，combined_attention=conflict_unresolved） | §2.1 / §2.3 / 存档 `B07/runs/run_086/run.json` |
| 第二步：主动制造一种真实失败，一次只制造一种 | **达成** | 只弄坏天气一条（`B07_WEATHER_UNAVAILABLE=1`）；其它三条证据的 status 仍为 ok；RAG 无命中与工具异常本轮**未做** | §1 / §2.3 证据 status 行 |
| 第三步：先不要修 —— 出现失败后不要“赶紧恢复 API”，继续运行同一个问题 | **达成** | 降级 run `run_088` 用的是**同一个固定问题、同一个 system prompt、同一套指纹**（question d3cc394bfcbe、prompt aa1400cf8568）；没有任何兜底数据补位 | §2.3 llm_context sha256 行 / 存档 llm_meta |
| 第四步：系统必须明确说出“还能判断什么”（不能整体输出“无法分析。”） | **达成** | 回答 483 字，整体输出“无法分析”：否；C 能力保持：历史 是、模型 是、知识 是（第 1 次；逐次明细见 §3.2） | §2.1 ② / §3.2 / §3.3 / §5 |
| 第五步：系统必须明确说出“现在不能判断什么” | **达成** | B 受限声明：未声明 0/2；⑥ 块写明“外部天气证据当前不可用 … 受到限制”；⑤ 限制字段新增 evidence_unavailable；⑦ 块新增“当前不可用的证据”一节 | §3.1 / §3.3 / §2.1 ④ |
| 第六步：比较前后两个 run_id | **达成** | `run_086` vs `run_088`：四问逐条回答 + 28 项逐项对比表；并追查了“这项证据原来到底有没有真正参与分析”（参与了，见 §2.2） | §2.1 / §2.2 / §2.3 |

## 5 S5 完成线逐条自评

> 文档完成线：“不整体崩溃、**不伪造缺失信息**，仍然利用剩余证据完成可能的判断，同时明确说明受到限制的部分。”

| 完成线 | 结论 | 实测证据 | 证据位置 |
|---|---|---|---|
| 不整体崩溃 | **达成** | 降级 run 状态 success；supporting 仍有 3 条证据；combined_attention = watch_internal；回答 483 字 | §2.1 ② / §3.2 |
| 不伪造缺失信息 | **达成** | A 伪造检查：判定伪造 0/2；⑥ 块内没有任何外部天气数值；⑦ 块把不可用证据单列（不进 supporting / opposing） | §3.1 / §3.3 |
| 仍然利用剩余证据完成可能的判断 | **达成** | C 能力保持：历史/模型/知识三项在第 1 次回答里分别为 是 / 是 / 是；回答整体输出“无法分析”：否 | §3.2 / §3.3 / §5.1 |
| 同时明确说明受到限制的部分 | **达成** | B 受限声明：未声明 0/2；external_conditions.status=unavailable、error 非空 | §3.1 / §3.3 / §2.1 ④ |

### 5.1 完成线里没有写、但值得记下来的一条

「知识 / 一般依据」在降级回答里**不是每次都出现**（逐次明细见 §3.2：知识维度 是、否）。文档第四步把“专业知识仍可能提供一般依据”列为**应当说清**的三项之一，但完成线只写了“不整体崩溃”，所以这一条**不判失败**，如实记录。

## 6 Limitations（诚实清单）

| 编号 | 限制 | 说明 |
|---|---|---|
| **L1** | 天气故障是**显式开关模拟**，不是真断网 | 真实网络故障的错误形态（超时 / 5xx / 限流 / 畸形 JSON）不在本轮覆盖范围；本轮只覆盖“provider 明确返回不可用”这一种形态。 |
| **L2** | 本轮只降级了天气一类证据 | RAG 无命中、其它工具异常**未做**（文档要求一次只做一种），因此不能据此推断“多条证据同时不可用”时的系统行为。 |
| **L3** | LLM 模型 S4 / S5 均为 `qwen-max` | 原 `qwen3.5-27b` 免费额度耗尽（HTTP 403 insufficient_quota），S4 起换用；跨阶段措辞不可直接比较。 |
| **L4** | LLM 解释层有采样波动 | 本轮每种状态跑多次并**全部计入分母**（不筛掉失败与异常样本）；但样本量仍然很小，单次结果不能当作稳定结论。 |
| **L5** | “还能判断什么 / 不能判断什么”由**上下文块**驱动（不改 SYSTEM_PROMPT） | 这依赖模型自觉遵循上下文，**不是提示词层面的硬保证**；实测证据：未在 ⑥/⑤ 块给出“必答”要求之前，降级回答有 1/3 次完全没提天气不可用，加上“必答”要求后才变成 3/3。S4 的 L6（提示词第 3 条与外部天气字面冲突）在 S5 **依然存在**。 |
| **L6** | 断言 / 伪造检测是**启发式多字短语 + 数值匹配** | 可能漏判（换一种说法就查不出来）或误判（把声明当成伪造）；A 项另外加了一层“这句话是在声明不可用、还是在陈述外部事实”的判定，这一层复用 S3 的元陈述判定，同样是启发式。 |
| **L7** | 降级 run 的 LLM 回答样本量有限 | “受限制声明”这个结论受采样波动影响；B 项结论应理解为“本轮样本内未出现未声明的情况”，不是“模型一定会声明”。 |
| **L8** | S4 的 run_023 存档 / 复算一致性 | 与 S5 无关，但影响整章证据链 —— 本轮已复核：`python -m B07.runs explain run_023` 输出“复算一致性：全部一致 ✅”，退出码 0（它渲染 19 项检查，文档口径写的是 22 项 —— 数量口径差，**不是**一致性失败）。 |
| **L9** | 外部条件是**离线构造用例**，不是实测天气 | S4 / S5 的外部天气来自 `B07/s3.py` 的离线夹具（provider 身份 `Offline-Fixture`）；基线与降级跑的是**同一个 provider**，所以组间对照成立，但绝对数值不可外推到真实大棚。 |
| **L10** | A 项的数值红线只覆盖夹具里的那几个数 | 模型若编造了**夹具里没有**的天气数值（例如“降水概率 80%”），本轮检测不到 —— 数值通道是**白名单**，不是全量数值扫描。 |

## 7 复现命令

```bash
# 1) 跑 S5 实验（每种状态 3 次；真实调 LLM）
./venv/Scripts/python.exe -m B07.s5

# 2) 只重放降级（不跑基线）：
./venv/Scripts/python.exe -c "import os; os.environ['B07_WEATHER_UNAVAILABLE']='1'; import B07.s5 as s; s.run_variant(degraded=True)"

# 3) 用已存档的回答原文重跑防伪造检验（离线、不调 LLM）
./venv/Scripts/python.exe -m B07.s5 --reprobe

# 4) 由原始结果生成报告（离线、不联网、不调 LLM，可确定性重放）
./venv/Scripts/python.exe B07/s5_report.py

# 5) 不变量测试
./venv/Scripts/python.exe -m pytest B07/tests -q

# 6) S1–S3 的存档逐值不变（S3 回归）
./venv/Scripts/python.exe -m B07.runs explain run_023
```

本报告的输入是 `s5_raw_result.json`；基线 run `run_086`、降级 run `run_088`（另有各 2 / 2 次重复运行，逐次明细见 §3.2）。

## 8 与 S1–S4 的关系：S4 的分歧结构在缺证据时怎么表现

S4 立起来的是**证据身份 + 方向 + 分歧**这一套结构。S5 把其中一条证据抽掉，问的是：这套结构在**缺一条腿**的时候还站不站得住。

| S4 建立的东西 | S5 里的表现 |
|---|---|
| 方向由程序规则判定（`judge_direction`） | 天气证据 status=unavailable → direction=**undetermined**，**没有**被伪造成 supporting/opposing |
| 三列互斥、不投票、不强制合并 | 降级后 supporting 3 / opposing 0 / undetermined 3，不可用证据只进 undetermined |
| 方向冲突时 `combined_attention = conflict_unresolved` | 冲突源消失后自动退让为 `watch_internal`，**规则一个字没改**（是 opposing 侧证据消失了，不是新增分支） |
| ⑦ 块是纯追加 | 降级时 ⑦ 块**多出**一节“当前不可用的证据”，①~⑥ 块结构不变 |
| 工具被调用过 ≠ 结果被采用 | `B05.get_weather_forecast` 被调用了，但结果 unavailable，明确记成“取不到”，不冒充已采用的数据 |

一句话：**S4 的分歧结构在缺证据时是“退让”，不是“崩溃”** —— 它不会替缺失的证据编一个方向，也不会因为少一条证据就整体拒答。

但也要说清楚 S5 **没有**证明的：“分歧结构在缺证据时表现得好”只在**这一类证据、这一个场景、这个模型**上成立过一次；它没有证明这套结构对任意证据缺失都稳健（见 L2）。
