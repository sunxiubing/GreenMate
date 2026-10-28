# B07 第三遍 · 视图四 | 场景回放与对比 —— 实测报告

> 本文所有数字**全部来自 `B07/runs/<run_id>/run.json` 的实际存档**，由 `B07/views_diff.diff_runs()` 生成，没有一处手工编造。
> 视图四的对比与归因**只读存档**：不调 LLM、不跑 B06、不取天气、不检索知识库、不重查 B02、不重跑判据。
> **只有回放器**（`B07/replay.py`）允许发起新运行。

## 0. 一句话结论

**同样两个 run，差异会落在哪一层、能被追到哪一个字段，是可以机器判定的**：
三层（输入 / 证据 / 状态）逐层比，归因规则 R1~R6 由差异组合推出，每条归因文本都引用**具体字段值** —— 没有一句「因为环境不同」。

## 1. 归因规则（R1~R6）

| 规则 | 含义 |
|---|---|
| **R1** | 输入变 + 证据变 + 状态变 → 完整因果链：输入变 → 证据变 → 状态变 |
| **R2** | 输入变 + 证据层无变化 + 状态变 → 差异来自输入/判据层 |
| **R3** | 输入变 + 证据变 + 状态没变 → 证据变了但状态没变（系统守恒/退让） |
| **R4** | 输入没变 + 证据没变 + 状态变了 → 条件没变结果却变了（**不稳定性**） |
| **R5** | 状态没变 → 本次条件下判断未变 |
| **R6** | （扩展）输入没变 + 证据变 + 状态变 → 差异出在证据层 |

★ **R4 是最重要的一条**：条件没变、证据没变，结果却变了 —— 这必须显式暴露为「不稳定性」，**不许**归成「因为环境不同」。

## 2. 四组对比的实测归因

| 组 | A | B | 输入变 | 证据变 | 状态变 | 命中规则 | instability |
|---|---|---|---|---|---|---|---|
| 组 1 | run_012 | run_015 | ✔ | ✘ | ✔ | **R2** | False |
| 组 2（文档指定） | run_015 | run_023 | ✔ | ✔ | ✔ | **R1** | False |
| 组 2（干净对照） | run_012 | run_023 | ✔ | ✔ | ✔ | **R1** | False |
| 组 3 | run_057 | run_084 | ✔ | ✔ | ✔ | **R1** | False |

### 组 1：S1↔S2（稳态 vs 渐进漂移）（run_012 ↔ run_015）

- 归因规则：**R2** —— 输入变 + 证据层无变化 + 状态变 → 差异来自输入/判据层
- 归因链：`输入变 → 证据层无可比台账（无法比较） → 状态变`
- attribution_complete = `True`　instability_detected = `False`　证据层可比 = `False`

**归因要点：**

- ★ 关键：**B06 预测两侧都是 NORMAL** —— 这个差别是**判据层**看出来的（稳定性判定 稳定 → 不稳定），模型层没看出来
- **台账层无法比较**（两侧均无证据台账），所以不能说「证据相同」，只能说「没有台账可比」

**变了的具体字段：**

- analysis_time：2026-10-21T16:00:00 → 2026-10-20T12:00:00
- current_state.lux：39842.0 → 59594.0
- current_state.timestamp：2026-10-21T16:00:00 → 2026-10-20T12:00:00
- history_window.start：2026-10-21T13:30:00 → 2026-10-20T09:30:00
- history_window.end：2026-10-21T16:00:00 → 2026-10-20T12:00:00
- history（序列逐点）：6 点（2026-10-21T13:30:00 → 2026-10-21T16:00:00） → 6 点（2026-10-20T09:30:00 → 2026-10-20T12:00:00）
- current_environment.lux：39842.0 → 59594.0
- recent_trend.stability_verdict：稳定 → 不稳定

**关键未变字段：**

- predicted_attention：NORMAL = NORMAL（未变）

### 组 2（文档指定）：S2↔S3（run_015 ↔ run_023）

- 归因规则：**R1** —— 输入变 + 证据变 + 状态变 → 完整因果链：输入变 → 证据变 → 状态变
- 归因链：`输入变 → 证据变 → 状态变`
- attribution_complete = `True`　instability_detected = `False`　证据层可比 = `False`

**归因要点：**

- 关注等级变了：— → watch_external
- ★ 关键：**B06 预测两侧都是 NORMAL** —— 这个差别是**判据层**看出来的（稳定性判定 不稳定 → 稳定），模型层没看出来
- **台账层无法比较**（两侧均无证据台账），所以不能说「证据相同」，只能说「没有台账可比」

**变了的具体字段：**

- analysis_time：2026-10-20T12:00:00 → 2026-10-21T16:00:00
- current_state.lux：59594.0 → 39842.0
- current_state.timestamp：2026-10-20T12:00:00 → 2026-10-21T16:00:00
- history_window.start：2026-10-20T09:30:00 → 2026-10-21T13:30:00
- history_window.end：2026-10-20T12:00:00 → 2026-10-21T16:00:00
- history（序列逐点）：6 点（2026-10-20T09:30:00 → 2026-10-20T12:00:00） → 6 点（2026-10-21T13:30:00 → 2026-10-21T16:00:00）
- external_conditions（是否存在）：不存在 → 存在
- external_conditions.signal：— → external_deteriorating
- 外部证据（是否存在）：不存在 → 存在
- 外部证据.signal：— → external_deteriorating
- current_environment.lux：59594.0 → 39842.0
- recent_trend.stability_verdict：不稳定 → 稳定
- combined_attention.value：— → watch_external

**关键未变字段：**

- predicted_attention：NORMAL = NORMAL（未变）

### 组 2（干净对照）：S1↔S3（只改「外部天气接入」这一项）（run_012 ↔ run_023）

- 归因规则：**R1** —— 输入变 + 证据变 + 状态变 → 完整因果链：输入变 → 证据变 → 状态变
- 归因链：`输入变 → 证据变 → 状态变`
- attribution_complete = `True`　instability_detected = `False`　证据层可比 = `False`

**归因要点：**

- 关注等级变了：— → watch_external
- ★ 关键：稳定性判定与 B06 预测（NORMAL）**都没变**，变的是关注等级（— → watch_external）—— 这正是外部条件接入带来的唯一效果
- **台账层无法比较**（两侧均无证据台账），所以不能说「证据相同」，只能说「没有台账可比」

**变了的具体字段：**

- external_conditions（是否存在）：不存在 → 存在
- external_conditions.signal：— → external_deteriorating
- 外部证据（是否存在）：不存在 → 存在
- 外部证据.signal：— → external_deteriorating
- combined_attention.value：— → watch_external

**关键未变字段：**

- recent_trend.stability_verdict：稳定 = 稳定（未变）
- predicted_attention：NORMAL = NORMAL（未变）

### 组 3：S4↔S5（外部证据从「可用且反向」变成「不可用」）（run_057 ↔ run_084）

- 归因规则：**R1** —— 输入变 + 证据变 + 状态变 → 完整因果链：输入变 → 证据变 → 状态变
- 归因链：`输入变 → 证据变 → 状态变`
- attribution_complete = `True`　instability_detected = `False`　证据层可比 = `True`

**归因要点：**

- 同一份外部证据从「ok」变成「unavailable」
- 分歧退化为缺失
- 系统退让到只看内部证据（关注等级 conflict_unresolved → watch_internal）
- ★ 关键：稳定性判定与 B06 预测（WATCH）**都没变**，变的是关注等级（conflict_unresolved → watch_internal）—— 变化来自**证据层**（外部证据失效 → 分歧退化为缺失 → 关注等级退让到只看内部证据）

**变了的具体字段：**

- external_conditions.signal：external_improving → external_unavailable
- 外部证据.signal：external_improving → external_unavailable
- 可用条数（status==ok）：4 → 3
- B03.status：ok → unavailable
- B03.direction：opposing → undetermined
- tool:B05.get_weather_forecast.status：ok → unavailable
- combined_attention.value：conflict_unresolved → watch_internal
- evidence_divergence.has_conflict：True → False
- evidence_divergence.opposing：B03 → —

**关键未变字段：**

- recent_trend.stability_verdict：不稳定 = 不稳定（未变）
- predicted_attention：WATCH = WATCH（未变）

## 3. 三层差异明细

### 组 1（run_012 ↔ run_015）

**第 1 层 · 输入** —— 整体：变了

| 字段 | A | B | 差异 |
|---|---|---|---|
| analysis_time | 2026-10-21T16:00:00 | 2026-10-20T12:00:00 | ✔ 变了 |
| current_state.temperature | 32.8 | 32.8 | ✘ 没变 |
| current_state.humidity | 56.7 | 56.7 | ✘ 没变 |
| current_state.lux | 39842.0 | 59594.0 | ✔ 变了 |
| current_state.timestamp | 2026-10-21T16:00:00 | 2026-10-20T12:00:00 | ✔ 变了 |
| history_window.start | 2026-10-21T13:30:00 | 2026-10-20T09:30:00 | ✔ 变了 |
| history_window.end | 2026-10-21T16:00:00 | 2026-10-20T12:00:00 | ✔ 变了 |
| history_window.count | 6 | 6 | ✘ 没变 |
| history（序列逐点） | 6 点（2026-10-21T13:30:00 → 2026-10-21T16:00:00） | 6 点（2026-10-20T09:30:00 → 2026-10-20T12:00:00） | ✔ 变了 |
| model_path | D:\GreenMate\B06\artifacts\b06-20261020-v1 | D:\GreenMate\B06\artifacts\b06-20261020-v1 | ✘ 没变 |
| model_version | b06-20261020-v1 | b06-20261020-v1 | ✘ 没变 |
| external_conditions（是否存在） | 不存在 | 不存在 | ✘ 没变 |
| external_conditions.signal | — | — | ✘ 没变 |

**第 2 层 · 证据** —— 整体：没变（可比：False）

| 字段 | A | B | 差异 |
|---|---|---|---|
| evidence_ledger（是否存在） | 无 | 无 | ✘ 没变 |
| 外部证据（是否存在） | 不存在 | 不存在 | ✘ 没变 |
| 外部证据.signal | — | — | ✘ 没变 |
| 证据条目 source_type 集合 | — | — | ✘ 没变 |
| 可用条数（status==ok） | 0 | 0 | ✘ 没变 |
| 台账 counts.adopted_entries（采用数，≠有效数） | — | — | ✘ 没变 |

> ★ 两侧均无证据台账 —— 台账部分「无法比较」，不是「证据相同」

**第 3 层 · 状态** —— 整体：变了

| 字段 | A | B | 差异 |
|---|---|---|---|
| current_environment.temperature | 32.8 | 32.8 | ✘ 没变 |
| current_environment.humidity | 56.7 | 56.7 | ✘ 没变 |
| current_environment.lux | 39842.0 | 59594.0 | ✔ 变了 |
| recent_trend.stability_verdict | 稳定 | 不稳定 | ✔ 变了 |
| predicted_attention | NORMAL | NORMAL | ✘ 没变 |
| combined_attention.value | — | — | ✘ 没变 |
| evidence_divergence.has_conflict | False | False | ✘ 没变 |
| evidence_divergence.supporting | — | — | ✘ 没变 |
| evidence_divergence.opposing | — | — | ✘ 没变 |
| evidence_divergence.undetermined | — | — | ✘ 没变 |

### 组 2（文档指定）（run_015 ↔ run_023）

**第 1 层 · 输入** —— 整体：变了

| 字段 | A | B | 差异 |
|---|---|---|---|
| analysis_time | 2026-10-20T12:00:00 | 2026-10-21T16:00:00 | ✔ 变了 |
| current_state.temperature | 32.8 | 32.8 | ✘ 没变 |
| current_state.humidity | 56.7 | 56.7 | ✘ 没变 |
| current_state.lux | 59594.0 | 39842.0 | ✔ 变了 |
| current_state.timestamp | 2026-10-20T12:00:00 | 2026-10-21T16:00:00 | ✔ 变了 |
| history_window.start | 2026-10-20T09:30:00 | 2026-10-21T13:30:00 | ✔ 变了 |
| history_window.end | 2026-10-20T12:00:00 | 2026-10-21T16:00:00 | ✔ 变了 |
| history_window.count | 6 | 6 | ✘ 没变 |
| history（序列逐点） | 6 点（2026-10-20T09:30:00 → 2026-10-20T12:00:00） | 6 点（2026-10-21T13:30:00 → 2026-10-21T16:00:00） | ✔ 变了 |
| model_path | D:\GreenMate\B06\artifacts\b06-20261020-v1 | D:\GreenMate\B06\artifacts\b06-20261020-v1 | ✘ 没变 |
| model_version | b06-20261020-v1 | b06-20261020-v1 | ✘ 没变 |
| external_conditions（是否存在） | 不存在 | 存在 | ✔ 变了 |
| external_conditions.signal | — | external_deteriorating | ✔ 变了 |

**第 2 层 · 证据** —— 整体：变了（可比：False）

| 字段 | A | B | 差异 |
|---|---|---|---|
| evidence_ledger（是否存在） | 无 | 无 | ✘ 没变 |
| 外部证据（是否存在） | 不存在 | 存在 | ✔ 变了 |
| 外部证据.signal | — | external_deteriorating | ✔ 变了 |
| 证据条目 source_type 集合 | — | — | ✘ 没变 |
| 可用条数（status==ok） | 0 | 0 | ✘ 没变 |
| 台账 counts.adopted_entries（采用数，≠有效数） | — | — | ✘ 没变 |

> ★ 两侧均无证据台账 —— 台账部分「无法比较」，不是「证据相同」

> ★ 外部证据这一层确实变了：A 没有外部条件，B 有外部条件（B 的 signal=external_deteriorating）

**第 3 层 · 状态** —— 整体：变了

| 字段 | A | B | 差异 |
|---|---|---|---|
| current_environment.temperature | 32.8 | 32.8 | ✘ 没变 |
| current_environment.humidity | 56.7 | 56.7 | ✘ 没变 |
| current_environment.lux | 59594.0 | 39842.0 | ✔ 变了 |
| recent_trend.stability_verdict | 不稳定 | 稳定 | ✔ 变了 |
| predicted_attention | NORMAL | NORMAL | ✘ 没变 |
| combined_attention.value | — | watch_external | ✔ 变了 |
| evidence_divergence.has_conflict | False | False | ✘ 没变 |
| evidence_divergence.supporting | — | — | ✘ 没变 |
| evidence_divergence.opposing | — | — | ✘ 没变 |
| evidence_divergence.undetermined | — | — | ✘ 没变 |

### 组 2（干净对照）（run_012 ↔ run_023）

**第 1 层 · 输入** —— 整体：变了

| 字段 | A | B | 差异 |
|---|---|---|---|
| analysis_time | 2026-10-21T16:00:00 | 2026-10-21T16:00:00 | ✘ 没变 |
| current_state.temperature | 32.8 | 32.8 | ✘ 没变 |
| current_state.humidity | 56.7 | 56.7 | ✘ 没变 |
| current_state.lux | 39842.0 | 39842.0 | ✘ 没变 |
| current_state.timestamp | 2026-10-21T16:00:00 | 2026-10-21T16:00:00 | ✘ 没变 |
| history_window.start | 2026-10-21T13:30:00 | 2026-10-21T13:30:00 | ✘ 没变 |
| history_window.end | 2026-10-21T16:00:00 | 2026-10-21T16:00:00 | ✘ 没变 |
| history_window.count | 6 | 6 | ✘ 没变 |
| history（序列逐点） | 6 点（2026-10-21T13:30:00 → 2026-10-21T16:00:00） | 6 点（2026-10-21T13:30:00 → 2026-10-21T16:00:00） | ✘ 没变 |
| model_path | D:\GreenMate\B06\artifacts\b06-20261020-v1 | D:\GreenMate\B06\artifacts\b06-20261020-v1 | ✘ 没变 |
| model_version | b06-20261020-v1 | b06-20261020-v1 | ✘ 没变 |
| external_conditions（是否存在） | 不存在 | 存在 | ✔ 变了 |
| external_conditions.signal | — | external_deteriorating | ✔ 变了 |

**第 2 层 · 证据** —— 整体：变了（可比：False）

| 字段 | A | B | 差异 |
|---|---|---|---|
| evidence_ledger（是否存在） | 无 | 无 | ✘ 没变 |
| 外部证据（是否存在） | 不存在 | 存在 | ✔ 变了 |
| 外部证据.signal | — | external_deteriorating | ✔ 变了 |
| 证据条目 source_type 集合 | — | — | ✘ 没变 |
| 可用条数（status==ok） | 0 | 0 | ✘ 没变 |
| 台账 counts.adopted_entries（采用数，≠有效数） | — | — | ✘ 没变 |

> ★ 两侧均无证据台账 —— 台账部分「无法比较」，不是「证据相同」

> ★ 外部证据这一层确实变了：A 没有外部条件，B 有外部条件（B 的 signal=external_deteriorating）

**第 3 层 · 状态** —— 整体：变了

| 字段 | A | B | 差异 |
|---|---|---|---|
| current_environment.temperature | 32.8 | 32.8 | ✘ 没变 |
| current_environment.humidity | 56.7 | 56.7 | ✘ 没变 |
| current_environment.lux | 39842.0 | 39842.0 | ✘ 没变 |
| recent_trend.stability_verdict | 稳定 | 稳定 | ✘ 没变 |
| predicted_attention | NORMAL | NORMAL | ✘ 没变 |
| combined_attention.value | — | watch_external | ✔ 变了 |
| evidence_divergence.has_conflict | False | False | ✘ 没变 |
| evidence_divergence.supporting | — | — | ✘ 没变 |
| evidence_divergence.opposing | — | — | ✘ 没变 |
| evidence_divergence.undetermined | — | — | ✘ 没变 |

### 组 3（run_057 ↔ run_084）

**第 1 层 · 输入** —— 整体：变了

| 字段 | A | B | 差异 |
|---|---|---|---|
| analysis_time | 2026-10-21T09:30:00 | 2026-10-21T09:30:00 | ✘ 没变 |
| current_state.temperature | 28.6 | 28.6 | ✘ 没变 |
| current_state.humidity | 66.9 | 66.9 | ✘ 没变 |
| current_state.lux | 42492.0 | 42492.0 | ✘ 没变 |
| current_state.timestamp | 2026-10-21T09:30:00 | 2026-10-21T09:30:00 | ✘ 没变 |
| history_window.start | 2026-10-21T07:00:00 | 2026-10-21T07:00:00 | ✘ 没变 |
| history_window.end | 2026-10-21T09:30:00 | 2026-10-21T09:30:00 | ✘ 没变 |
| history_window.count | 6 | 6 | ✘ 没变 |
| history（序列逐点） | 6 点（2026-10-21T07:00:00 → 2026-10-21T09:30:00） | 6 点（2026-10-21T07:00:00 → 2026-10-21T09:30:00） | ✘ 没变 |
| model_path | D:\GreenMate\B06\artifacts\b06-20261020-v1 | D:\GreenMate\B06\artifacts\b06-20261020-v1 | ✘ 没变 |
| model_version | b06-20261020-v1 | b06-20261020-v1 | ✘ 没变 |
| external_conditions（是否存在） | 存在 | 存在 | ✘ 没变 |
| external_conditions.signal | external_improving | external_unavailable | ✔ 变了 |

**第 2 层 · 证据** —— 整体：变了（可比：True）

| 字段 | A | B | 差异 |
|---|---|---|---|
| evidence_ledger（是否存在） | 有 | 有 | ✘ 没变 |
| 外部证据（是否存在） | 存在 | 存在 | ✘ 没变 |
| 外部证据.signal | external_improving | external_unavailable | ✔ 变了 |
| 证据条目 source_type 集合 | B02, B03, B04, B06 | B02, B03, B04, B06 | ✘ 没变 |
| 可用条数（status==ok） | 4 | 3 | ✔ 变了 |
| 台账 counts.adopted_entries（采用数，≠有效数） | 4 | 4 | ✘ 没变 |
| B02.status | ok | ok | ✘ 没变 |
| B02.direction | supporting | supporting | ✘ 没变 |
| B03.status | ok | unavailable | ✔ 变了 |
| B03.direction | opposing | undetermined | ✔ 变了 |
| B04.status | ok | ok | ✘ 没变 |
| B04.direction | supporting | supporting | ✘ 没变 |
| B06.status | ok | ok | ✘ 没变 |
| B06.direction | supporting | supporting | ✘ 没变 |
| tool:B02.get_environment_history.status | ok | ok | ✘ 没变 |
| tool:B02.get_environment_history.adopted | true | true | ✘ 没变 |
| tool:B05.get_current_environment.status | ok | ok | ✘ 没变 |
| tool:B05.get_current_environment.adopted | false | false | ✘ 没变 |
| tool:B05.get_weather_forecast.status | ok | unavailable | ✔ 变了 |
| tool:B05.get_weather_forecast.adopted | true | true | ✘ 没变 |
| tool:B05.search_tomato_knowledge.status | ok | ok | ✘ 没变 |
| tool:B05.search_tomato_knowledge.adopted | true | true | ✘ 没变 |
| tool:B06.predict_window.status | ok | ok | ✘ 没变 |
| tool:B06.predict_window.adopted | true | true | ✘ 没变 |

**第 3 层 · 状态** —— 整体：变了

| 字段 | A | B | 差异 |
|---|---|---|---|
| current_environment.temperature | 28.6 | 28.6 | ✘ 没变 |
| current_environment.humidity | 66.9 | 66.9 | ✘ 没变 |
| current_environment.lux | 42492.0 | 42492.0 | ✘ 没变 |
| recent_trend.stability_verdict | 不稳定 | 不稳定 | ✘ 没变 |
| predicted_attention | WATCH | WATCH | ✘ 没变 |
| combined_attention.value | conflict_unresolved | watch_internal | ✔ 变了 |
| evidence_divergence.has_conflict | True | False | ✔ 变了 |
| evidence_divergence.supporting | B02, B04, B06 | B02, B04, B06 | ✘ 没变 |
| evidence_divergence.opposing | B03 | — | ✔ 变了 |
| evidence_divergence.undetermined | B03, B04 | B03, B04 | ✘ 没变 |

## 4. 组 2 为什么做了两组

文档指定的 **S2↔S3（run_015 ↔ run_023）不是干净对照**：

| 字段 | run_015 | run_023 | 差异 |
|---|---|---|---|
| history_window.start | 2026-10-20T09:30:00 | 2026-10-21T13:30:00 | ✔ 变了 |
| history_window.end | 2026-10-20T12:00:00 | 2026-10-21T16:00:00 | ✔ 变了 |
| history（序列逐点） | 6 点（2026-10-20T09:30:00 → 2026-10-20T12:00:00） | 6 点（2026-10-21T13:30:00 → 2026-10-21T16:00:00） | ✔ 变了 |
| external_conditions（是否存在） | 不存在 | 存在 | ✔ 变了 |

→ 历史窗口变了、稳定判定变了，**混进了不止一个变量**。

**真正干净的对照是 S1↔S3（run_012 ↔ run_023）**：

| 字段 | run_012 | run_023 | 差异 |
|---|---|---|---|
| analysis_time | 2026-10-21T16:00:00 | 2026-10-21T16:00:00 | ✘ 没变 |
| current_state.temperature | 32.8 | 32.8 | ✘ 没变 |
| current_state.humidity | 56.7 | 56.7 | ✘ 没变 |
| current_state.lux | 39842.0 | 39842.0 | ✘ 没变 |
| current_state.timestamp | 2026-10-21T16:00:00 | 2026-10-21T16:00:00 | ✘ 没变 |
| history_window.start | 2026-10-21T13:30:00 | 2026-10-21T13:30:00 | ✘ 没变 |
| history_window.end | 2026-10-21T16:00:00 | 2026-10-21T16:00:00 | ✘ 没变 |
| history_window.count | 6 | 6 | ✘ 没变 |
| history（序列逐点） | 6 点（2026-10-21T13:30:00 → 2026-10-21T16:00:00） | 6 点（2026-10-21T13:30:00 → 2026-10-21T16:00:00） | ✘ 没变 |
| model_path | D:\GreenMate\B06\artifacts\b06-20261020-v1 | D:\GreenMate\B06\artifacts\b06-20261020-v1 | ✘ 没变 |
| model_version | b06-20261020-v1 | b06-20261020-v1 | ✘ 没变 |
| external_conditions（是否存在） | 不存在 | 存在 | ✔ 变了 |
| external_conditions.signal | — | external_deteriorating | ✔ 变了 |

→ **只有「外部条件是否存在」这一项变了**，history 逐点相同、稳定判定未变、B06 预测未变，变的只有关注等级：

- 归因文本：★ 关键：稳定性判定与 B06 预测（NORMAL）**都没变**，变的是关注等级（— → watch_external）—— 这正是外部条件接入带来的唯一效果

> ★ 页面上必须同时展示这两组并说明差别：「文档指定的 S2↔S3 混入了历史窗口与稳定判定的变化；S1↔S3 才是只改『外部天气接入』这一项的干净对照。」

## 5. 回放自检：同一条件下重跑结果不一致

回放自检的判定条件：**inputs.changed == False 且 state.changed == True** → 归因必须标为「同一条件下重跑结果不一致（不稳定性）」，并列出具体不一致的字段。

在现有存档里，**S1/S2/S3 的 run 之间没有出现过这种情况**（三组对比的 inputs 都是变化的）；该分支由 `B07/tests/test_views_compare.py::test_r4_instability_is_detected` 用**临时构造**的两个 run（同输入、只改状态层）验证，断言归因规则必须是 **R4**、文本必须含「同一条件下重跑结果不一致（不稳定性）」、且**不得**出现「因为环境不同」。

## 6. 四视图统一检查（文档 p.69）

靶子 run：**run_082**

| 编号 | 判定 | 结果 | 实际 |
|---|---|---|---|
| U1 | 身份字段在五个地方完全一致 | **PASS** | 五处一致：视图一 view_data=run_082,2026-10-21T09:30:00,aa1400cf8568,d3cc394bfcbe / 视图二 view_evolution_data=run_082,2026-10-21T09:30:00,aa1400cf8568,d3cc394bfcbe / 视图三 view_evidence_data=run_082,2026-10-21T09:30:00,aa1400cf8568,d3cc394bfcbe / 视图四 view_compare_data(A)=run_082,2026-10-21T09:30:00,aa1400cf8568,d3cc394bfcbe / 视图四 view_compare_data(B)=run_082,2026-10-21T09:30:00,aa1400cf8568,d3cc394bfcbe |
| U2 | 四个视图读盘的 run.json 的 sha256 相同 | **PASS** | 读盘 5 次，sha256=5775b4b88d275b8f…，路径唯一=True |
| U3 | 零重算：打桩后四个视图仍能装配 + 渲染 | **PASS** | 打桩 openai、httpx、requests、B03.weather、B05.get_weather_forecast、B06.inference、B04.retrieve、B02.database.store、socket 后 8 步全部成功 |
| U4 | 视图一/二/三既有断言仍然通过 | **PASS** | 本次检查跳过了子进程跑测试（run_tests=False） |

- `identity_consistent` = `True`
- `source_sha256` = `5775b4b88d275b8ffa76976933cecc44276208cb398aa4ad066252a077aefdab`
- `zero_recompute` = `True`

> U3 的「零重算」是把 openai / httpx / requests / B03 天气 / B05 天气接口 / B06 推理 / B04 知识检索 / B02 取样 / socket **全部打桩成「一调用就抛」**，四个视图的装配 + 渲染（8 步）仍然全部成功。

## 7. 终端渲染实测（节选）

### 组 1：`python -m B07.views compare run_012 run_015`

```text
==============================================================================
 B07 · 视图四 | 场景回放与对比
 对比 A = run_012 (S1)      对比 B = run_015 (S2)
 A 提示词指纹=aa1400cf8568   B 提示词指纹=aa1400cf8568
 A 问题指纹=d3cc394bfcbe     B 问题指纹=d3cc394bfcbe
 A 数据 sha256=e1bd6c495e1a（本次渲染）   只读存档，不重算
------------------------------------------------------------------------------ 【一】五项并排（当前状态 / 近期趋势 / 预测 / 证据 / 最终判断）
   当前状态    A: 温度32.8℃ 湿度56.7% 光照39842.0lux @10-21T16:00
   当前状态    B: 温度32.8℃ 湿度56.7% 光照59594.0lux @10-20T12:00
   近期趋势    A: 稳定（窗口3 小时 / 6 点）
   近期趋势    B: 不稳定（窗口3 小时 / 6 点）
   预测        A: NORMAL
   预测        B: NORMAL
   证据        A: 无台账（该 run 早于 S4 的证据层）
   证据        B: 无台账（该 run 早于 S4 的证据层）
   最终判断    A: —（该 run 无 combined_attention 字段）
   最终判断    B: —（该 run 无 combined_attention 字段）
------------------------------------------------------------------------------ 【二】差异归因
   输入变了：
     ✔ 变了：
          · analysis_time：2026-10-21T16:00:00 → 2026-10-20T12:00:00
          · current_state.lux：39842.0 → 59594.0
          · current_state.timestamp：2026-10-21T16:00:00 → 2026-10-20T12:00:00
          · history_window.start：2026-10-21T13:30:00 → 2026-10-20T09:30:00
          · history_window.end：2026-10-21T16:00:00 → 2026-10-20T12:00:00
          · history（序列逐点）：6/6 点不同（不同）
     ✘ 没变：
          · current_state.temperature：32.8 → 32.8
          · current_state.humidity：56.7 → 56.7
          · history_window.count：6 → 6
          · model_path：D:\GreenMate\B06\artifacts\b06-20261020-v1 → D:\GreenMate\B06\artifacts\b06-20261020-v1
          · model_version：b06-20261020-v1 → b06-20261020-v1
          · external_conditions（是否存在）：不存在 → 不存在
          · external_conditions.signal：— → —
   证据变了：
     ✘ 变了：（无）
     ✘ 没变：
          · evidence_ledger（是否存在）：无 → 无
          · 外部证据（是否存在）：不存在 → 不存在
          · 外部证据.signal：— → —
          · 证据条目 source_type 集合：— → —
          · 可用条数（status==ok）：0 → 0
          · 台账 counts.adopted_entries（采用数，≠有效数）：— → —
          ★ 两侧均无证据台账 —— 台账部分「无法比较」，不是「证据相同」
   状态变了：
     ✔ 变了：
          · current_environment.lux：39842.0 → 59594.0
          · recent_trend.stability_verdict：稳定 → 不稳定
     ✘ 没变：
          · current_environment.temperature：32.8 → 32.8
          · current_environment.humidity：56.7 → 56.7
          · predicted_attention：NORMAL → NORMAL
          · combined_attention.value：— → —
          · evidence_divergence.has_conflict：False → False
          · evidence_divergence.supporting：— → —
          · evidence_divergence.opposing：— → —
          · evidence_divergence.undetermined：— → —
   归因链  ：输入变 → 证据层无可比台账（无法比较） → 状态变
   ★ 关键：**B06 预测两侧都是 NORMAL** —— 这个差别是**判据层**看出来的（稳定性判定 稳定 → 不稳定），模型层没看出来
   **台账层无法比较**（两侧均无证据台账），所以不能说「证据相同」，只能说「没有台账可比」
   变了的具体字段：
     · analysis_time：2026-10-21T16:00:00 → 2026-10-20T12:00:00
     · current_state.lux：39842.0 → 59594.0
     · current_state.timestamp：2026-10-21T16:00:00 → 2026-10-20T12:00:00
     · history_window.start：2026-10-21T13:30:00 → 2026-10-20T09:30:00
     · history_window.end：2026-10-21T16:00:00 → 2026-10-20T12:00:00
     · history（序列逐点）：6 点（2026-10-21T13:30:00 → 2026-10-21T16:00:00） → 6 点（2026-10-20T09:30:00 → 2026-10-20T12:00:00）
     · current_environment.lux：39842.0 → 59594.0
     · recent_trend.stability_verdict：稳定 → 不稳定
   归因规则  ：R2 —— 输入变 + 证据层无变化 + 状态变 → 差异来自输入/判据层
   attribution_complete = True
   instability_detected = False（同一条件下重跑结果不一致）
==============================================================================
```

### 组 2：`python -m B07.views compare run_012 run_023`

```text
==============================================================================
 B07 · 视图四 | 场景回放与对比
 对比 A = run_012 (S1)      对比 B = run_023 (S3_with)
 A 提示词指纹=aa1400cf8568   B 提示词指纹=aa1400cf8568
 A 问题指纹=d3cc394bfcbe     B 问题指纹=d3cc394bfcbe
 A 数据 sha256=0803b0896cc1（本次渲染）   只读存档，不重算
------------------------------------------------------------------------------ 【一】五项并排（当前状态 / 近期趋势 / 预测 / 证据 / 最终判断）
   当前状态    A: 温度32.8℃ 湿度56.7% 光照39842.0lux @10-21T16:00
   当前状态    B: 温度32.8℃ 湿度56.7% 光照39842.0lux @10-21T16:00
   近期趋势    A: 稳定（窗口3 小时 / 6 点）
   近期趋势    B: 稳定（窗口3 小时 / 6 点）
   预测        A: NORMAL
   预测        B: NORMAL
   证据        A: 无台账（该 run 早于 S4 的证据层）
   证据        B: 无台账（该 run 早于 S4 的证据层）
   最终判断    A: —（该 run 无 combined_attention 字段）
   最终判断    B: watch_external（内部 B06 预测为 NORMAL，但外部条件判定为 external_deteriorating，关注度由外部信号抬升）
------------------------------------------------------------------------------ 【二】差异归因
   输入变了：
     ✔ 变了：
          · external_conditions（是否存在）：不存在 → 存在
          · external_conditions.signal：— → external_deteriorating
     ✘ 没变：
          · analysis_time：2026-10-21T16:00:00 → 2026-10-21T16:00:00
          · current_state.temperature：32.8 → 32.8
          · current_state.humidity：56.7 → 56.7
          · current_state.lux：39842.0 → 39842.0
          · current_state.timestamp：2026-10-21T16:00:00 → 2026-10-21T16:00:00
          · history_window.start：2026-10-21T13:30:00 → 2026-10-21T13:30:00
          · history_window.end：2026-10-21T16:00:00 → 2026-10-21T16:00:00
          · history_window.count：6 → 6
          · history（序列逐点）：0/6 点不同（逐点相同）
          · model_path：D:\GreenMate\B06\artifacts\b06-20261020-v1 → D:\GreenMate\B06\artifacts\b06-20261020-v1
          · model_version：b06-20261020-v1 → b06-20261020-v1
   证据变了：
     ✔ 变了：
          · 外部证据（是否存在）：不存在 → 存在
          · 外部证据.signal：— → external_deteriorating
     ✘ 没变：
          · evidence_ledger（是否存在）：无 → 无
          · 证据条目 source_type 集合：— → —
          · 可用条数（status==ok）：0 → 0
          · 台账 counts.adopted_entries（采用数，≠有效数）：— → —
          ★ 两侧均无证据台账 —— 台账部分「无法比较」，不是「证据相同」
          ★ 外部证据这一层确实变了：A 没有外部条件，B 有外部条件（B 的 signal=external_deteriorating）
   状态变了：
     ✔ 变了：
          · combined_attention.value：— → watch_external
     ✘ 没变：
          · current_environment.temperature：32.8 → 32.8
          · current_environment.humidity：56.7 → 56.7
          · current_environment.lux：39842.0 → 39842.0
          · recent_trend.stability_verdict：稳定 → 稳定
          · predicted_attention：NORMAL → NORMAL
          · evidence_divergence.has_conflict：False → False
          · evidence_divergence.supporting：— → —
          · evidence_divergence.opposing：— → —
          · evidence_divergence.undetermined：— → —
   归因链  ：输入变 → 证据变 → 状态变
   关注等级变了：— → watch_external
   ★ 关键：稳定性判定与 B06 预测（NORMAL）**都没变**，变的是关注等级（— → watch_external）—— 这正是外部条件接入带来的唯一效果
   **台账层无法比较**（两侧均无证据台账），所以不能说「证据相同」，只能说「没有台账可比」
   变了的具体字段：
     · external_conditions（是否存在）：不存在 → 存在
     · external_conditions.signal：— → external_deteriorating
     · 外部证据（是否存在）：不存在 → 存在
     · 外部证据.signal：— → external_deteriorating
     · combined_attention.value：— → watch_external
   归因规则  ：R1 —— 输入变 + 证据变 + 状态变 → 完整因果链：输入变 → 证据变 → 状态变
   attribution_complete = True
   instability_detected = False（同一条件下重跑结果不一致）
==============================================================================
```

### 组 3：`python -m B07.views compare run_057 run_084`

```text
==============================================================================
 B07 · 视图四 | 场景回放与对比
 对比 A = run_057 (S4)      对比 B = run_084 (S5)
 A 提示词指纹=aa1400cf8568   B 提示词指纹=aa1400cf8568
 A 问题指纹=d3cc394bfcbe     B 问题指纹=d3cc394bfcbe
 A 数据 sha256=7357610f6b08（本次渲染）   只读存档，不重算
------------------------------------------------------------------------------ 【一】五项并排（当前状态 / 近期趋势 / 预测 / 证据 / 最终判断）
   当前状态    A: 温度28.6℃ 湿度66.9% 光照42492.0lux @10-21T09:30
   当前状态    B: 温度28.6℃ 湿度66.9% 光照42492.0lux @10-21T09:30
   近期趋势    A: 不稳定（窗口3 小时 / 6 点）
   近期趋势    B: 不稳定（窗口3 小时 / 6 点）
   预测        A: WATCH
   预测        B: WATCH
   证据        A: 4/4 条可用（按 status==ok 统计）
   证据        B: 3/4 条可用（按 status==ok 统计）
   最终判断    A: conflict_unresolved（证据方向不一致（支持 3 条 / 相反 1 条），系统保留分歧，不做强制合并，也不做投票）
   最终判断    B: watch_internal（内部 B06 预测为 WATCH，按内部信号直接提升关注度；外部信号 external_unavailable 不改变这一结论）
------------------------------------------------------------------------------ 【二】差异归因
   输入变了：
     ✔ 变了：
          · external_conditions.signal：external_improving → external_unavailable
     ✘ 没变：
          · analysis_time：2026-10-21T09:30:00 → 2026-10-21T09:30:00
          · current_state.temperature：28.6 → 28.6
          · current_state.humidity：66.9 → 66.9
          · current_state.lux：42492.0 → 42492.0
          · current_state.timestamp：2026-10-21T09:30:00 → 2026-10-21T09:30:00
          · history_window.start：2026-10-21T07:00:00 → 2026-10-21T07:00:00
          · history_window.end：2026-10-21T09:30:00 → 2026-10-21T09:30:00
          · history_window.count：6 → 6
          · history（序列逐点）：0/6 点不同（逐点相同）
          · model_path：D:\GreenMate\B06\artifacts\b06-20261020-v1 → D:\GreenMate\B06\artifacts\b06-20261020-v1
          · model_version：b06-20261020-v1 → b06-20261020-v1
          · external_conditions（是否存在）：存在 → 存在
   证据变了：
     ✔ 变了：
          · 外部证据.signal：external_improving → external_unavailable
          · 可用条数（status==ok）：4 → 3
          · B03.status：ok → unavailable
          · B03.direction：opposing → undetermined
          · tool:B05.get_weather_forecast.status：ok → unavailable
     ✘ 没变：
          · evidence_ledger（是否存在）：有 → 有
          · 外部证据（是否存在）：存在 → 存在
          · 证据条目 source_type 集合：B02, B03, B04, B06 → B02, B03, B04, B06
          · 台账 counts.adopted_entries（采用数，≠有效数）：4 → 4
          · B02.status：ok → ok
          · B02.direction：supporting → supporting
          · B04.status：ok → ok
          · B04.direction：supporting → supporting
          · B06.status：ok → ok
          · B06.direction：supporting → supporting
          · tool:B02.get_environment_history.status：ok → ok
          · tool:B02.get_environment_history.adopted：true → true
          · tool:B05.get_current_environment.status：ok → ok
          · tool:B05.get_current_environment.adopted：false → false
          · tool:B05.get_weather_forecast.adopted：true → true
          · tool:B05.search_tomato_knowledge.status：ok → ok
          · tool:B05.search_tomato_knowledge.adopted：true → true
          · tool:B06.predict_window.status：ok → ok
          · tool:B06.predict_window.adopted：true → true
   状态变了：
     ✔ 变了：
          · combined_attention.value：conflict_unresolved → watch_internal
          · evidence_divergence.has_conflict：True → False
          · evidence_divergence.opposing：B03 → —
     ✘ 没变：
          · current_environment.temperature：28.6 → 28.6
          · current_environment.humidity：66.9 → 66.9
          · current_environment.lux：42492.0 → 42492.0
          · recent_trend.stability_verdict：不稳定 → 不稳定
          · predicted_attention：WATCH → WATCH
          · evidence_divergence.supporting：B02, B04, B06 → B02, B04, B06
          · evidence_divergence.undetermined：B03, B04 → B03, B04
   归因链  ：输入变 → 证据变 → 状态变
   同一份外部证据从「ok」变成「unavailable」
   分歧退化为缺失
   系统退让到只看内部证据（关注等级 conflict_unresolved → watch_internal）
   ★ 关键：稳定性判定与 B06 预测（WATCH）**都没变**，变的是关注等级（conflict_unresolved → watch_internal）—— 变化来自**证据层**（外部证据失效 → 分歧退化为缺失 → 关注等级退让到只看内部证据）
   变了的具体字段：
     · external_conditions.signal：external_improving → external_unavailable
     · 外部证据.signal：external_improving → external_unavailable
     · 可用条数（status==ok）：4 → 3
     · B03.status：ok → unavailable
     · B03.direction：opposing → undetermined
     · tool:B05.get_weather_forecast.status：ok → unavailable
     · combined_attention.value：conflict_unresolved → watch_internal
     · evidence_divergence.has_conflict：True → False
     · evidence_divergence.opposing：B03 → —
   归因规则  ：R1 —— 输入变 + 证据变 + 状态变 → 完整因果链：输入变 → 证据变 → 状态变
   attribution_complete = True
   instability_detected = False（同一条件下重跑结果不一致）
==============================================================================
```

## 8. Limitations

- **L1** 回放**不是可复现的**：真实天气走真实世界时间，同一个场景两次回放的外部条件可能不同；LLM 采样本身也有波动。离线构造的天气可复现，但那是**构造用例**，不是实时天气。
- **L2** 回放会**真的调用 LLM 并落盘新 run**，消耗额度；对比与归因部分完全离线、只读存档。
- **L3** 归因是**基于存档字段的规则推导**，不是因果推断。「输入变了」不等于「输入变了一定导致了判断变」，只能说「本次存档里差异出现在这一层」。
- **L4** R4 说明系统存在**输出不稳定**。这是 S3 已观察到的采样波动在产品层的复现，**不应**被解读为判断错误。
- **L5** 文档指定的 S2↔S3 在本档案中**不是单一变量对照**（历史窗口与稳定判定同时变了）；真正干净的对照是 S1↔S3。页面上必须同时展示两组并说明差别。
- **L6** S1/S2/S3 的 26 个 run 没有证据台账，S1↔S2 的「台账层」只能判为**无法比较**，不能判为「证据相同」。
- **L7** 统一检查只覆盖「四个视图讲的是同一次分析」这一件事，**不检查判断本身对不对**。
- **L8** 三组对比各只取了 **1 个代表 run**（S4 有 19 个同构 run、S5 有 44 个、内含 3 种状态），结论来自代表样本，**不等于**对全部带台账 run 的普查结论。

## 9. 跑法

```bash
python -m B07.views compare run_012 run_015   # 组 1
python -m B07.views compare run_015 run_023   # 组 2（文档指定）
python -m B07.views compare run_012 run_023   # 组 2（干净对照）
python -m B07.views compare run_057 run_084   # 组 3
python -m B07.views check run_082            # 四视图统一检查
python -m B07.replay S1 --dry-run             # 回放计划（不调 LLM、不落盘）
```

Web 版：`python -m B07.views_web --port 8010`，页面切到「视图四 | 场景回放与对比」，接口 `GET /api/compare?a=&b=`。
