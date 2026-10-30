# B08 第三遍 · 证明二 | 主动制造一次真实失败

> 生成时间：2026-10-30
> 制造命令：`python -m B07.replay S5 --degraded --offline`
> 判定命令：`python -m B07.b08_trace --failure run_126 run_124`
> **本遍唯一新跑的一次**：消耗 1 次 LLM 额度，产生**新 run_id = `run_126`**。

## 0. 这次失败是怎么制造的

| | |
|---|---|
| 入口 | `B07.s5.run_variant(degraded=True)`（`B07/replay.py` 调它） |
| 失败开关 | 环境变量 **`B07_WEATHER_UNAVAILABLE=1`**（`B07/weather.py:WEATHER_UNAVAILABLE_ENV`） |
| 开关效果 | 天气 provider 直接返回一个 `status=unavailable` 的结果，**不发起真实天气请求** |
| 为什么不用断网 | 断网不可复现（代理、其它进程、时区），而且会把"天气不可用"和"网络整体坏了"混在一起 |
| 硬约束 | 失败必须显式：`status=unavailable`、`data=None`、`error` 写明原因；**不许留上次成功的值、不许写空 error、不许静默兜底** |

### ★ `run_126` 与已有的 S5 降级 run 的区分

仓库里已有约 20 个 `scenario="S5"` 的降级 run（`run_084`、`run_125` 等），
那些是 **B07 开发期产物**。**`run_126` 是 B08 第三遍的正式失败验证记录** ——
它是本遍**当场制造**的一次失败，不是从旧存档里挑的。

| | run_id | 来源 | 何时产生 |
|---|---|---|---|
| B07 开发期 | `run_084` | B07 开发期存档 | 早于 B08 |
| B08 第二遍 | `run_125` | 第二遍固定场景回放（S5.json） | 2026-10-30 |
| **B08 第三遍** | **`run_126`** | **本遍主动制造** | **2026-10-30** |

---

## 1. 存档摘要（`run_126`）

| 字段 | 值 |
|---|---|
| scenario | S5（降级变体） |
| analysis_time | 2026-10-21T09:30:00 |
| location | A区-3号温室 |
| 问题指纹 | `d3cc394bfcbe` |
| 提示词指纹 | `aa1400cf8568` |
| model_version | b06-20261020-v1 |
| LLM | qwen-max，elapsed_ms=9506，content_length=367，total_tokens=3978 |

---

## 2. 四项检查

### ✅ F-1 工具留下了失败记录

来源：`evidence_ledger.tool_calls.records`

| tool_name | status | adopted | error |
|---|---|---|---|
| `B02.get_environment_history` | ok | True | — |
| `B05.get_current_environment` | ok | False | — |
| `B06.predict_window` | ok | True | — |
| **`B05.get_weather_forecast`** | **unavailable** | True | **weather provider unavailable (B07_WEATHER_UNAVAILABLE=1): 模拟天气 API 故障，未发起真实天气请求** |
| `B05.search_tomato_knowledge` | ok | True | — |

**判定**：天气那次调用 `status == "unavailable"` 且 `error` 非空 → **✅ 通过**。

### ✅ F-2 证据变为 unavailable

来源：`evidence_ledger.entries`

| evidence_id | source_type | status | direction |
|---|---|---|---|
| EV-B02-01 | B02 | ok | supporting |
| EV-B06-01 | B06 | ok | supporting |
| **EV-B03-01** | **B03** | **unavailable** | **undetermined** |
| EV-B04-01 | B04 | ok | supporting |

B03 那条的 error 原文：

```
weather provider unavailable (B07_WEATHER_UNAVAILABLE=1): 模拟天气 API 故障，未发起真实天气请求
```

**判定**：`status == "unavailable"` 且 `direction == "undetermined"` → **✅ 通过**。

### ✅ F-3 最终结果明确显示限制（且不伪造天气事实）

来源：`llm_explanation` 原文

> 根据当前的模拟数据，2026年10月21日9:30时，大棚内的环境状态为：温度28.6℃，湿度66.9%，光照42492.0 lux。最近3小时内的数据显示环境不稳定，具体表现为温度波动较大（从24.2℃到28.6℃，波动4.4℃，超过阈值2.0℃），湿度显著下降（从92.6%降至66.9%，变化-25.7%，超过阈值3.0%），且湿度单步最大变化为6.2%，也超过了阈值1.5%。此外，湿度全程单调下降，不符合稳定判据。
>
> 基于B02历史数据和B06短时预测模型（版本b06-20261020-v1）的输出，建议提高关注程度至WATCH级别。模型预测的信心度为1.0，表明存在需要关注的变化趋势。
>
> 需要注意的是，本次分析所依据的数据全部为模拟数据，非真实传感器读数；**外部天气证据当前不可用，因此与外部天气影响相关的判断受到限制。**

| 检查 | 结果 |
|---|---|
| 出现限定说明（「不可用」/「判断受到限制」） | ✅ 两处都命中：`不可用`、`判断受到限制` |
| **不能出现天气事实断言** | ✅ **0 处命中**（"降水概率将升至…" / "外部天气显示…" 这类一个都没有） |

**判定**：免责句是**期望出现**的，不是违规；同时不能伪造天气事实 → **✅ 通过**。

另外，结构化判断里也生成了对应的限制条目 `evidence_unavailable`：

> 以下证据源当前不可用，与它们相关的判断受到限制：B03（provider=Offline-Fixture）的证据当前不可用（status=unavailable，失败原因：weather provider unavailable (B07_WEATHER_UNAVAILABLE=1): 模拟天气 API 故障，未发起真实天气请求）。**其余仍可用的证据（B02 历史、B06 预测等）继续参与本次判断**，本次结论只在这些证据能支撑的范围内成立。回答中必须明确说明这一限制。

### ✅ F-4 其他仍正常的能力继续运行

| 检查 | 结果 |
|---|---|
| B02 / B06 / B04 三条 status 仍 == ok | ✅ `{'B02': 'ok', 'B06': 'ok', 'B04': 'ok'}` |
| `combined_attention.value` != None | ✅ `watch_internal`（系统没有因为一条证据挂了就整体崩） |
| `stability_verdict` 仍可计算 | ✅ `不稳定` |
| `llm_explanation` 仍含基于内部证据的结论 | ✅ 引用了「波动 4.4℃，超过阈值 2.0℃」「变化-25.7%，超过阈值 3.0%」「也超过了阈值 1.5%」「不符合稳定判据」 |

**判定** → **✅ 通过**。

> ★ 这一条是"不整体失效"的关键：**一条外部证据挂了，内部证据链照常工作，
> 稳定判定照常算，关注等级照常给（`watch_internal`），结论照常有内部依据。**

---

## 3. 反向对照：变化确实来自那一条证据失效

基线变体（不降级）= **`run_124`**（第二遍正式 S4 固定场景回放：同一锚点、
同一 history、同一 model_output，**唯一差别是 B03 证据可用**）。

| | 基线 `run_124` | 失败 `run_126` |
|---|---|---|
| B03 status | **ok** | **unavailable** |
| B03 direction | opposing | undetermined |
| `has_conflict` | **True** | **False** |
| `combined_attention` | **conflict_unresolved** | **watch_internal** |
| B02 / B06 / B04 | ok / ok / ok | ok / ok / ok |
| 稳定判定 | 不稳定 | 不稳定（没变） |
| B06 预测 | WATCH | WATCH（没变） |

**⇒ 变化确实来自那一条证据失效**，不是别的地方变了：
除 B03 之外的所有证据状态、判定、模型输出**都一模一样**。

（文档靶子对同样成立：`run_057` 的 B03=ok/opposing、CA=conflict_unresolved、
has_conflict=True ↔ `run_084` 的 B03=unavailable/undetermined、CA=watch_internal、
has_conflict=False —— 这就是证明一的组 D。）

---

## 4. 结论

> ★ **失败验证不是"把错误藏掉"。而是证明：错误发生以后，整个系统仍然知道自己
> 当前处于什么状态。**
>
> `run_126` 里：错误被显式记录在工具台账（status + error 原文）、
> 证据被显式降级（status=unavailable、direction=undetermined）、
> 最终回答明确写出限制（"外部天气证据当前不可用，因此与外部天气影响相关的判断受到限制"）、
> **而且没有凭空编造天气事实**；同时内部证据链（B02/B06/B04）照常运行，
> 稳定判定照常算，关注等级照常给（`watch_internal`），结论照常有内部判据支撑。

---

## 本材料的 Limitations

* **L5** 证明二**只制造了"天气不可用"一种失败**；参数错误、超时、空结果、
  知识检索失败**尚未覆盖**。
* **L7** 本遍只有这一次主动失败运行消耗 LLM 额度（1 次）；其余判定全是离线复算。
* **L3/L4** 同证明一：离线判定不落盘、不产生 run、不进索引；
  判定沿用存档里的 `model_output`。
* 本次制造的失败是**离线夹具 + 显式开关**，不是真实 API 故障 ——
  真实链路上的天气 API 失败本轮**未覆盖**（仍是第二遍就记下的缺口）。
