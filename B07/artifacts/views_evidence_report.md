# B07 第三遍 · 视图三 | 证据空间 —— 实测报告

> 本文所有数字**全部来自 `B07/runs/<run_id>/run.json` 的实际存档**，由 `B07/views.py` 的 `view_evidence_data()` / `render_evidence()` 生成，没有一处手工编造。
> 视图三**只读存档**：不调 LLM、不跑 B06、不取天气、不检索知识库、不重查 B02、不重跑判据。

## 0. 一句话结论

**只看证据空间就能知道三件事**：当前判断有哪些依据（4/4 条有效）、哪些依据方向不同（run_082 的天气是「反向」，其余三份是「支持」）、哪些依据现在缺失（run_084 的天气 unavailable —— **照常显示**，并写清为什么不可用、影响了什么判断）。**

## 1. 三份渲染的摘要

### 1.1 run_082 —— 分歧齐全（对照组是 run_106）

| 项 | 实测值 |
| --- | --- |
| scenario | S5 |
| analysis_time | 2026-10-21T09:30:00 |
| 证据条数（entries） | 4 |
| **可用条数（status==ok）** | **4** |
| 不可用条数 | 0 |
| 台账 counts.adopted_entries（采用数，≠有效数） | 4 |
| B02 | status=ok　direction=supporting（支持）　source=B02/environment_samples |
| B06 | status=ok　direction=supporting（支持）　source=B06/b06-20261020-v1 |
| B03 | status=ok　direction=opposing（反向）　source=B03/Open-Meteo（离线构造用例） |
| B04 | status=ok　direction=supporting（支持）　source=B04/knowledge_base |
| combined_attention.value | conflict_unresolved |
| external_signal | external_improving |
| evidence_divergence.has_conflict | True |

### 1.2 run_084 —— 天气证据缺失

| 项 | 实测值 |
| --- | --- |
| scenario | S5 |
| 证据条数（entries） | 4 |
| **可用条数（status==ok）** | **3** |
| 不可用条数 | 1（EV-B03-01 / B03） |
| 台账 counts.adopted_entries（采用数，≠有效数） | 4 |
| 缺失条目的 status | unavailable |
| 缺失条目的 error 原文 | `weather provider unavailable (B07_WEATHER_UNAVAILABLE=1): 模拟天气 API 故障，未发起真实天气请求` |
| combined_attention.value | watch_internal |
| external_signal | external_unavailable |

> ★ **同一个台账里 `counts.adopted_entries = 4`，但真正有效的只有 3 条**。「可用条数 / 完整度」必须按 `status == "ok"` 统计；用 `adopted_entries` 会把降级场景显示成「4/4 完整」，恰好把最该暴露的缺失抹平。

### 1.3 run_106 —— 对照组（四份证据方向一致）

| 项 | 实测值 |
| --- | --- |
| scenario | S5 |
| 可用条数（status==ok） | 4 |
| B02 | direction=supporting（支持） |
| B06 | direction=supporting（支持） |
| B03 | direction=supporting（支持） |
| B04 | direction=supporting（支持） |
| evidence_divergence.has_conflict | False |
| combined_attention.value | watch_internal |
| external_signal | external_deteriorating |

## 2. C1~C14 机器判定（全部 PASS）

| 编号 | 判定 | 结果 | 实测值 |
| --- | --- | --- | --- |
| C1 | 四份证据的 direction 不全相同（分歧存在） | **PASS** | {"B02": "supporting", "B06": "supporting", "B03": "opposing", "B04": "supporting"} |
| C2 | B02=supporting 且 B06=supporting | **PASS** | B02=supporting, B06=supporting |
| C3 | B03=opposing 且 external_conditions.signal==external_improving | **PASS** | B03=opposing, signal=external_improving |
| C4 | B04=supporting | **PASS** | B04=supporting |
| C5 | combined_attention.value == conflict_unresolved | **PASS** | conflict_unresolved |
| C6 | evidence_divergence.has_conflict == True | **PASS** | True |
| C7 | ★ 反抹平：渲染同时出现「支持」与「反向」，且不出现抹平表述 | **PASS** | 含「支持」=True　含「反向」=True　抹平表述命中=无 |
| C8 | run_084 的渲染里必须出现 B03（不许把不可用的证据过滤掉） | **PASS** | B03 出现 |
| C9 | 渲染里出现 status=unavailable / 不可用 | **PASS** | 出现 |
| C10 | 渲染里出现 error 原文 'weather provider unavailable' | **PASS** | 出现 |
| C11 | 出现「它影响了什么判断」，内容来自 unavailable_context / combined_attention.reason | **PASS** | signal=external_unavailable；reason 原文已出现在影响段落：True |
| C12 | ★ 可用条数==3（按 status==ok），不得==counts.adopted_entries(4) | **PASS** | usable=3　adopted_entries=4 |
| C13 | run_084 的 combined_attention.value == watch_internal | **PASS** | watch_internal |
| C14 | 对照组 run_106：四份 direction 全为 supporting、has_conflict == False | **PASS** | {"B02": "supporting", "B06": "supporting", "B03": "supporting", "B04": "supporting"}　has_conflict=False |

**合计 14/14 条 PASS。**

## 3. 四类证据的「三问」回答清单

每份证据固定回答三个问题：**它从哪里来？它说了什么？它现在是否有效？**下面照抄 run_082 的渲染结果。

### 历史证据 · B02　方向徽章 [支持]　status=ok

**它从哪里来？**

- 证据编号 EV-B02-01
- 来源 source = B02/environment_samples
- 分析时刻 time = 2026-10-21T09:30:00
- 窗口采样点数 = 6

**它说了什么？**

- 稳定判据结论：近 3 小时温度 24.2~28.6℃，温度波动 4.4℃ 超过阈值 2.0℃（超出 2.4）；湿度首尾变化 -25.7% 超过阈值 3.0%（超出 22.7）；湿度单步最大变化 6.2% 超过阈值 1.5%（超出 4.7）；湿度全程单调下降（判据禁止全程单调） → 判定为不稳定（湿度持续单向变化）
- stability_passed = False
- 窗口温度序列 = [24.2, 25.8, 25.5, 27.7, 27.8, 28.6]
- 判据「温度最大波动」：实测 4.4 / 阈值 2.0 → 未通过（24.2~28.6℃，波动 4.4℃）
- 判据「湿度首尾总变化」：实测 -25.7 / 阈值 3.0 → 未通过（首 92.6% → 尾 66.9%，变化 -25.7%）
- 判据「湿度单步最大变化」：实测 6.2 / 阈值 1.5 → 未通过（单步最大 6.2%）
- 判据「光照单步最大跳变」：实测 11191.0 / 阈值 15000 → 通过（单步最大 11191 lux）
- 判据「湿度无持续单向趋势」：实测 全程单调下降 / 阈值 禁止全程单调 → 未通过（全程单调下降）

**它现在是否有效？**

- status = ok
- 方向徽章 = 支持
- 这一条现在有效（取数成功，判定已完成）

**它影响了什么判断？**

- [支持] 内部状态不稳定、末段温度上升 —— 稳定判据未通过（近 3 小时温度 24.2~28.6℃，温度波动 4.4℃ 超过阈值 2.0℃（超出 2.4）；湿度首尾变化 -25.7% 超过阈值 3.0%（超出 22.7）；湿度单步最大变化 6.2% 超过阈值 1.5%（超出 4.7）；湿度全程单调下降（判据禁止全程单调） → 判定为不稳定（湿度持续单向变化））且末段温度上升

### 模型证据 · B06　方向徽章 [支持]　status=ok

**它从哪里来？**

- 证据编号 EV-B06-01
- 来源 source = B06/b06-20261020-v1
- 分析时刻 time = 2026-10-21T09:30:00
- model_version = b06-20261020-v1
- 窗口 window_id = s1-live-001

**它说了什么？**

- 模型输出 predicted_attention = WATCH
- 置信度 confidence = 1.0
- 特征数 feature_count = 33（logistic_coefficient_ranking）
- 主要贡献特征 current_lux：coefficient=1.114976（推向WATCH）
- 主要贡献特征 slope_lux：coefficient=0.742619（推向WATCH）
- 主要贡献特征 max_lux：coefficient=-0.625809（推向NORMAL）
- 主要贡献特征 min_lux：coefficient=-0.397984（推向NORMAL）
- 主要贡献特征 std_lux：coefficient=-0.371169（推向NORMAL）
- 模型评估指标（存档自带）：accuracy=0.7448979591836735，precision=0.5909090909090909，recall=0.4482758620689655，f1=0.5098039215686274

**它现在是否有效？**

- status = ok
- 方向徽章 = 支持
- 这一条现在有效（取数成功，判定已完成）

**它影响了什么判断？**

- [支持] 模型输出 WATCH（confidence=1.0, model_version=b06-20261020-v1） —— B06 在该窗口上真实输出 WATCH

### 天气证据 · B03　方向徽章 [反向]　status=ok

**它从哪里来？**

- 证据编号 EV-B03-01
- 来源 source = B03/Open-Meteo（离线构造用例）
- 取得时间 retrieved_at = 2026-10-24T00:00:00+00:00
- provider = Offline-Fixture
- 预报点 forecast_time = 2026-10-25T04:00（窗口 6h）
- 地点 = 河北固安
- 数据性质标签 = 构造用例（不是实测）（is_constructed=True）

**它说了什么？**

- 外部信号 signal = external_improving
- 判定方法 judge_method = relative_delta
- 判定依据 basis：相对基线差分（主判定）：降水概率 50.0% → 10.0%（Δ=-40.0 个百分点，阈值 ±30.0 个百分点） → external_improving；温度 Δ-1.0℃、云量 Δ-70.0%（只作描述，不参与判定）
- 基线点(now)：2026-10-24T22:00 温度 15.0℃ / 湿度 70.0% / 降水概率 50.0% / 云量 90.0% / 小雨
- 预报点：2026-10-25T04:00 温度 14.0℃ / 湿度 70.0% / 降水概率 10.0% / 云量 20.0% / 晴朗
- 降水概率差 precip_delta = -40.0
- 温度差 temp_delta = -1.0
- 云量差 cloud_delta = -70.0

**它现在是否有效？**

- status = ok
- 方向徽章 = 反向
- 这一条现在有效（取数成功，判定已完成）

**它影响了什么判断？**

- [反向] 外部条件判定 external_improving（relative_delta） —— 外部条件判定为 external_improving：相对基线差分（主判定）：降水概率 50.0% → 10.0%（Δ=-40.0 个百分点，阈值 ±30.0 个百分点） → external_improving；温度 Δ-1.0℃、云量 Δ-70.0%（只作描述，不参与判定）
- [未定] 外部天气取的是窗口末时刻单点值

### 知识证据 · B04　方向徽章 [支持]　status=ok

**它从哪里来？**

- 证据编号 EV-B04-01
- 来源 source = B04/knowledge_base
- 检索时刻 time = 2026-10-26T15:01:45.681881+00:00（★ 这是知识检索时刻，与分析时刻可能差好几天）
- 检索式 query = 过期数据导致温室调控决策基于过时信息
- top_k = 5

**它说了什么？**

- 命中 CH-003-02（SRC-003，score=0.3626655720824688）章节「静默返回旧数据的危害」页 ./docs/b03_weather.md
-     摘录：静默返回旧数据（phantom data）危害：下游系统以为拿到最新有效数据；温室调控决策基于过时信息可能导致作物受损；问题隐蔽，潜伏数天才被发现；违反 API 隐含契约：成功响应代表数据完整
- 命中 CH-003-01（SRC-003，score=0.06655222249031584）章节「HTTP 400 不返回旧缓存的原因」页 ./docs/b03_weather.md
-     摘录：HTTP 400 不返回旧缓存：400 是请求参数错误（纬度格式错误），不是临时性故障。重新发请求（修正参数后）一定能成功，所以不需要降级到过期数据。Fail Loudly 原则要求显式失败而非静默降级
- 命中 CH-002-04（SRC-002，score=0.03422238306915283）章节「drought 扰动完整生命周期」页 ./B02/simulator/engine.py
-     摘录：drought 扰动完整生命周期：进入阶段 6 小时（smoothstep 过渡）→ 稳定持续 12 小时（humidity_delta=-10.0 全量生效）→ 恢复阶段 6 小时（smoothstep 过渡）→ 回到正常基线
- 命中 CH-001-03（SRC-001，score=0.01866426424378078）章节「PUBLIC 不用于 A/B 实验的原因」页 ./docs/b01_b03_design.md
-     摘录：PUBLIC 数据不用于 A/B 对照：Open-Meteo 每次返回实时预报不同，间隔内值可能变化；1分钟粒度的天气预报分辨率不是真正瞬时状态；HTTP 400/429 时 PUBLIC 返回 None，无法保证 A/B 两边有效状态
- 命中 CH-001-04（SRC-001，score=0.017458747334187674）章节「format_state 来源标注差异」页 ./docs/b01_b03_design.md
-     摘录：format_state() 根据 data_source 参数区分：data_source='PUBLIC' 时追加 '_weather：该数据来自 Open-Meteo 实时天气 API，非模拟数据'；data_source='SIMU

**它现在是否有效？**

- status = ok
- 方向徽章 = 支持
- 这一条现在有效（取数成功，判定已完成）

**它影响了什么判断？**

- [支持] 知识检索 '过期数据导致温室调控决策基于过时信息' 命中 5 条，主命中 CH-003-02（score=0.3626655720824688） —— 该条目提供『决策基于不完整信息可能导致作物受损』这一支持依据
- [未定] 知识证据只支持『需要关注』

## 4. 缺失态（run_084 的天气块）

**它从哪里来？**

- 证据编号 EV-B03-01
- 来源 source = B03/Open-Meteo（离线构造用例）
- 取得时间 retrieved_at = 2026-10-24T00:00:00+00:00
- provider = Offline-Fixture
- 预报点 forecast_time = None（窗口 6h）
- 地点 = 河北固安
- 数据性质标签 = 构造用例（不是实测）（is_constructed=True）

**它说了什么？**

- （没有内容 —— 这次取数失败了）

**它现在是否有效？**

- status = unavailable
- 方向徽章 = 未定
- 这一条现在**无效**：取数失败，没有参与判定
- ★ 它是「证据存在但不可用」—— 照常显示，不许当作没有这条证据

**为什么不可用？**

- 失败原因（存档 error 原文）：weather provider unavailable (B07_WEATHER_UNAVAILABLE=1): 模拟天气 API 故障，未发起真实天气请求
- 警告原文：外部天气不可用（unavailable/error）；forecast 点取数失败：weather provider unavailable (B07_WEATHER_UNAVAILABLE=1): 模拟天气 API 故障，未发起真实天气请求
- 警告原文：基线点（now）取数失败：weather provider unavailable (B07_WEATHER_UNAVAILABLE=1): 模拟天气 API 故障，未发起真实天气请求
- 警告原文：外部天气为 PUBLIC 真实数据，forecast_time 属于真实世界时间轴；B02 内部为 SIMULATED 模拟时间轴（2026-10-15T02:00:00 ~ 2026-10-22T01:30:00），两者非同一时间基准，本次仅将天气用作外部条件方向参考，不做时刻级跨源对齐。

**它影响了什么判断？**

- 外部判定未做出：judge_method = unavailable，signal = external_unavailable，future_ok = False，basis：预报点不可用，未做任何判定
- 因此合并关注等级仍为 watch_internal（internal=WATCH，external_signal=external_unavailable，has_conflict=False）
- 存档里的判定理由：内部 B06 预测为 WATCH，按内部信号直接提升关注度；外部信号 external_unavailable 不改变这一结论

## 5. Limitations

- **L1** 视图三读的是**存档里那一次**的证据台账，不是实时重新取数；刷新页面不会重新调 LLM / 不重跑 B06 / 不再取一次天气。
- **L2** 「证据可用度」按 `status == "ok"` 统计，是**证据链可审计性**的代理指标，不等于「分析质量评分」；`counts.adopted_entries` 是「被采用为证据条目的数」，**不是**有效条数（降级场景 adopted=4 但只有 3 条有效）。
- **L3** B04 知识证据的取得时间是**知识检索时刻**，与分析时刻可能差好几天（run_082 的检索时刻是 2026-10-26T15:01:45.681881+00:00，分析时刻是 2026-10-21T09:30:00）；界面显示真实时间，不做对齐。
- **L4** 天气 caveats 只覆盖本次实测情形（夜间、solar_radiation 恒 0、高温分支未覆盖），不是通用天气结论。
- **L5** 外部天气走真实 API 的真实时间轴，内部 B02 是模拟时间轴，两者不可时刻级对齐，天气只作方向参考。
- **L6** S1/S2/S3 的 24 个 run 早于证据层，没有台账，证据空间对这些 run 不可用（明确标注，不假装有证据）。

## 6. 跑法

```bash
python -m B07.views evidence run_082     # 分歧齐全
python -m B07.views evidence run_084     # 天气缺失
python -m B07.views evidence run_106     # 对照组
python -m B07.views evidence run_012     # 没有台账
```

Web 版：`python -m B07.views_web --port 8010`，页面切到「视图三 | 证据空间」，接口 `GET /api/evidence?run_id=`。
