# B07 第二遍 · 创新点 2 —— 把"多源信息"真正变成"可追溯证据"

> 本轮性质：**审计 + 证明**，不是开发。
> 只新增 `B07/innovation2.py` 与本报告，**未改动 B07 现有任何代码**；
> **未新增任何 LLM 调用**（全部基于 S4/S5 已落盘的 run 存档）；
> 未改 SYSTEM_PROMPT / 固定问题 / 任何判据阈值；未碰 B01–B06。

## 0 一句话结论

**证明了**：以 `run_082` 为准，LLM 回答里的结论句可以逐条**反着追回到
run.json 里的具体字段**（结论句 → `structured_judgement.<字段>` →
`evidence_ledger.entries[i]` → 那次**真实发生**的工具调用 → 工具名 + arguments + status）；
四类信息在台账里**各自带身份**（source / time / status / result / direction / adopted），
"这条结论**没有**哪类证据支撑"、"哪些证据在反向提示"同样能追到条目 ——
也就是**「很多信息」已经变成「一组各自有身份、各自有作用的证据」**。

**没证明**：① 「四类作用不可互换」是语义判断，机器只能验证"有没有分别标注"，
不能证明"确实不可替代"；② 黑盒 A/B 对照沿用 S4-3 的数据，**那组数据本身分不出高下**
（三个主比率在 A/B 两组都是 `0/0` 与 `1.0`，见第 5 节），本轮**没有重新跑**，
所以这一项只能如实记录，不能拿它当证据。

## 1 四类信息 → 四份证据 的对照表

（数据源：`B07/runs/run_082/run.json` 的 `evidence_ledger.entries`）

| 角色 | source_type | 来源 | 时间（取得时刻） | 可用性 | 结果 | 各自承担什么作用 |
| --- | --- | --- | --- | --- | --- | --- |
| history | B02 | `B02/environment_samples` | `2026-10-21T09:30:00` | status=`ok`，direction=`supporting`，adopted=`True` | 内部状态不稳定、末段温度上升 | 已发生：窗口内的实测/模拟采样 |
| weather | B03 | `B03/Open-Meteo（离线构造用例）` | `2026-10-21T09:30:00` | status=`ok`，direction=`opposing`，adopted=`True` | 外部条件判定 external_improving（relative_delta） | 可能发生的外部条件：预报点，不是已发生事实 |
| knowledge | B04 | `B04/knowledge_base` | `2026-10-26T15:01:45.681881+00:00` | status=`ok`，direction=`supporting`，adopted=`True` | 知识检索 '过期数据导致温室调控决策基于过时信息' 命中 5 条，主命中 CH-003-02（score=0.3626655720824688） | 一般性依据：知识库切片，不是本温室实测结论 |
| model | B06 | `B06/b06-20261020-v1` | `2026-10-21T09:30:00` | status=`ok`，direction=`supporting`，adopted=`True` | 模型输出 WATCH（confidence=1.0, model_version=b06-20261020-v1） | 下一窗口预测：模型输出，不是已发生事实 |

四类**作用不可互换**，各自的结构化依据：

| 角色 | 作用 | 结构化字段证据（不是 basis 文本） | 该 run 里的实际值 |
| --- | --- | --- | --- |
| history | 已发生 | `result.history` / `result.current_state` / `result.stability_conclusion` | temps=`[24.2, 25.8, 25.5, 27.7, 27.8, 28.6]`，stability_passed=`False` |
| weather | 可能发生的外部条件 | `result.forecast_point` / `result.baseline_point` / `result.signal` / `ext.forecast_time` | signal=`external_improving`，forecast_time=`2026-10-25T04:00` |
| knowledge | 一般性依据 | `result.citations` / `result.query` / `ext.citation` | query=`过期数据导致温室调控决策基于过时信息`，chunk=`CH-003-02` |
| model | 下一窗口预测 | `result.predicted_attention` / `result.window_id` / `result.confidence` | predicted_attention=`WATCH`，window_id=`s1-live-001` |

> **重要提醒（文档第二步）**：天气预报**不是**已发生的历史事实；模型输出**不是**专业知识；
> RAG 文档**不是**下一窗口一定会发生什么的证明。台账用 `direction` 字段把
> 「支持 / 反向 / 仍不足以确定」分开了 —— 本 run 的 `evidence_divergence.each_source` =
> `{"B02": "supporting", "B06": "supporting", "B03": "opposing", "B04": "supporting"}`，
> supporting 3 条 / opposing 1 条 /
> undetermined 2 条。

## 2 溯源链实录（run.json 实际字段值）

**结论句**（claim_index=2）

> 在过去的3小时内，环境状态被判定为不稳定。

- 挑句规则：含判断/建议类动词 判定；证据特征词 3 个（history）；台账数值命中 0 个；得分 3
- 命中角色：history
- 最强匹配强度：`strong`（strong=数值命中或命中有区分度的特征词；weak=只命中「温度/湿度/模型」这类通用词）
- 缺哪类证据支撑：weather, knowledge, model

- 证据 `EV-B02-01`（role=history，source_type=B02，direction=supporting）
    - `structured_judgement` 字段路径：structured_judgement.evidence_divergence.supporting[0]
    - `evidence_ledger.entries[0]` → source=`B02/environment_samples`，time=`2026-10-21T09:30:00`，status=`ok`
    - 内容摘要：内部状态不稳定、末段温度上升
    - 依据：稳定判据未通过（近 3 小时温度 24.2~28.6℃，温度波动 4.4℃ 超过阈值 2.0℃（超出 2.4）；湿度首尾变化 -25.7% 超过阈值 3.0%（超出 22.7）；湿度单步最大变化 6.2% 超过阈值 1.5%（超出 4.7）；湿度全程单调下降（判据禁止全程单调） → 判定为不稳定（湿度持续单向变化））且末段温度上升
    - 工具调用：B02.get_environment_history，status=`ok`，arguments=`{"start": "2026-10-21T07:00:00", "end": "2026-10-21T09:30:00"}`
    - 匹配信号：关键词（关键词 ['过去', '不稳定', '稳定']，数值 []）→ 强度 `strong`
    - 该调用与证据的对应依据：['start=2026-10-21T07:00:00', 'end=2026-10-21T09:30:00']

- chain_ok = True
- 反向提示与限制（同样能追到台账条目）：
    - `EV-B03-01`（role=weather，direction=opposing，status=`ok`） `evidence_ledger.entries[2]`：外部条件判定 external_improving（relative_delta） ｜ 外部条件判定为 external_improving：相对基线差分（主判定）：降水概率 50.0% → 10.0%（Δ=-40.0 个百分点，阈值 ±30.0 个百分点） → external_improving；温度 Δ-1.0℃、云量 Δ-70.0%（只作描述，不参与判定） ｜ 工具 B05.get_weather_forecast（status=`ok`）

---

**结论句**（claim_index=4）

> 基于B06模型（版本b06-20261020-v1）的预测，系统建议提高关注程度至WATCH级别，置信度为1.0。

- 挑句规则：含判断/建议类动词 建议、预测；证据特征词 6 个（model）；台账数值命中 1 个；得分 7
- 命中角色：model
- 最强匹配强度：`strong`（strong=数值命中或命中有区分度的特征词；weak=只命中「温度/湿度/模型」这类通用词）
- 缺哪类证据支撑：history, weather, knowledge

- 证据 `EV-B06-01`（role=model，source_type=B06，direction=supporting）
    - `structured_judgement` 字段路径：structured_judgement.evidence_divergence.supporting[1]
    - `evidence_ledger.entries[1]` → source=`B06/b06-20261020-v1`，time=`2026-10-21T09:30:00`，status=`ok`
    - 内容摘要：模型输出 WATCH（confidence=1.0, model_version=b06-20261020-v1）
    - 依据：B06 在该窗口上真实输出 WATCH
    - 工具调用：B06.predict_window，status=`ok`，arguments=`{"window_id": "s1-live-001", "model_version": "b06-20261020-v1"}`
    - 匹配信号：关键词+数值（关键词 ['B06', '模型', '预测', 'WATCH', '置信度', '关注程度']，数值 ['1.0']）→ 强度 `strong`
    - 该调用与证据的对应依据：['window_id=s1-live-001', 'model_version=b06-20261020-v1']

- chain_ok = True
- 反向提示与限制（同样能追到台账条目）：
    - `EV-B03-01`（role=weather，direction=opposing，status=`ok`） `evidence_ledger.entries[2]`：外部条件判定 external_improving（relative_delta） ｜ 外部条件判定为 external_improving：相对基线差分（主判定）：降水概率 50.0% → 10.0%（Δ=-40.0 个百分点，阈值 ±30.0 个百分点） → external_improving；温度 Δ-1.0℃、云量 Δ-70.0%（只作描述，不参与判定） ｜ 工具 B05.get_weather_forecast（status=`ok`）

### 限制陈述也能追到字段

限制类句子不是台账证据，但同样指向 run.json 里的具体字段
（`structured_judgement.limitations.<key>`）：

- 「该模型仅适用于3小时至1小时的窗口，并且只覆盖温度、湿度和光照三项指标。」 → `structured_judgement.limitations.model_boundary`（线索词 [['仅适用', '只覆盖']]）
- 「外部天气条件显示未来降水概率降低，但这些信息仅供方向性参考，不参与内部数值的直接比较与判断。」 → `structured_judgement.limitations.no_weather_no_kb`（线索词 [['不参与', '仅供', '方向性参考']]）

## 3 第四步「真调用」检查（V3 / V4 逐条判定）

**文档第四步的判据**：如果最终报告写着"根据天气信息……"，但运行记录里根本没有
天气工具调用，这一轮不通过。

### V3 红线（天气）

- 触发条件：回答里出现 ['天气', '降水', '外部'] → 命中天气相关表述
- 判定：**✅ 通过**
- 命中的工具调用记录（来自 `evidence_ledger.tool_calls.records`）：

```json
[
  {
    "tool_name": "B05.get_weather_forecast",
    "status": "ok",
    "arguments": {
      "location": "河北固安",
      "forecast_window": "6h",
      "note": "内部调用 now + 6h 两次，取基线点与预报点"
    },
    "adopted": true,
    "error": null
  }
]
```

### V4 知识/检索

- 触发条件：回答里出现 （无） → **未触发**（本 run 的回答没谈知识，V4 无触发）
- 判定：**✅ 通过**
- 命中的工具调用记录：

```json
[
  {
    "tool_name": "B05.search_tomato_knowledge",
    "status": "ok",
    "arguments": {
      "query": "过期数据导致温室调控决策基于过时信息"
    },
    "adopted": true,
    "error": null
  }
]
```

> `run_057` 是本轮特意带上的对照：它的回答里**真的出现了"知识"**，
> 所以 V4 在它身上有一个真实触发点（见第 6 节判定表）。

### 工具调用总账（`run_082`）

| tool_name | status | adopted | arguments |
| --- | --- | --- | --- |
| `B02.get_environment_history` | `ok` | `True` | `{"start": "2026-10-21T07:00:00", "end": "2026-10-21T09:30:00"}` |
| `B05.get_current_environment` | `ok` | `False` | `{"location": "A区-3号温室"}` |
| `B06.predict_window` | `ok` | `True` | `{"window_id": "s1-live-001", "model_version": "b06-20261020-v1"}` |
| `B05.get_weather_forecast` | `ok` | `True` | `{"location": "河北固安", "forecast_window": "6h", "note": "内部调用 now + 6h 两次，取基线点与预报点"}` |
| `B05.search_tomato_knowledge` | `ok` | `True` | `{"query": "过期数据导致温室调控决策基于过时信息"}` |

台账自己的 `adoption.text`：**本次调用了 5 个工具，其中 4 条结果被采用为证据**。
未被采用的那一条：`[{"tool_name": "B05.get_current_environment", "status": "ok", "purpose": "取库内最新一条（仅作 latest_in_db 参考）", "arguments": {"location": "A区-3号温室"}, "reason": "仅作 latest_in_db 参考，不参与本次方向判定，未升格为证据"}]`。

## 4 第五步「为什么需要关注」的四句拆解

1. **历史持续升温 → 支持「变化正在形成」**
   - 证据：`EV-B02-01`（role=history，`B02/environment_samples`，status=`ok`）
   - 实际字段值：stability_passed=`False`，temps=`[24.2, 25.8, 25.5, 27.7, 27.8, 28.6]`
   - 依据：稳定判据未通过（近 3 小时温度 24.2~28.6℃，温度波动 4.4℃ 超过阈值 2.0℃（超出 2.4）；湿度首尾变化 -25.7% 超过阈值 3.0%（超出 22.7）；湿度单步最大变化 6.2% 超过阈值 1.5%（超出 4.7）；湿度全程单调下降（判据禁止全程单调） → 判定为不稳定（湿度持续单向变化））且末段温度上升
2. **模型输出 WATCH → 支持下一窗口继续关注**
   - 证据：`EV-B06-01`（role=model，`B06/b06-20261020-v1`，status=`ok`）
   - 实际字段值：predicted_attention=`WATCH`，confidence=`1.0`，model_version=`b06-20261020-v1`，window_id=`s1-live-001`
   - 依据：B06 在该窗口上真实输出 WATCH
3. **天气显示未来可能降温 → 不能把「继续恶化」说得太确定**
   - 证据：`EV-B03-01`（role=weather，`B03/Open-Meteo（离线构造用例）`，status=`ok`，direction=`opposing`）
   - 实际字段值：signal=`external_improving`，precip_delta=`-40.0`，forecast_point=`{"timestamp": "2026-10-25T04:00", "temperature": 14.0, "humidity": 70.0, "precipitation_probability": 10.0, "cloud_cover": 20.0, "solar_radiation": 0.0, "weather_code": 0, "weather_description": "晴朗"}`，forecast_time=`2026-10-25T04:00`
   - 依据：外部条件判定为 external_improving：相对基线差分（主判定）：降水概率 50.0% → 10.0%（Δ=-40.0 个百分点，阈值 ±30.0 个百分点） → external_improving；温度 Δ-1.0℃、云量 Δ-70.0%（只作描述，不参与判定）
4. **专业知识只支持持续高温值得关注 → 不能证明未来一定恶化**
   - 证据：`EV-B04-01`（role=knowledge，`B04/knowledge_base`，status=`ok`，direction=`supporting`）
   - 实际字段值：query=`过期数据导致温室调控决策基于过时信息`，主命中 citation=`{"chunk_id": "CH-003-02", "source_id": "SRC-003", "section": "静默返回旧数据的危害", "page": "./docs/b03_weather.md", "score": 0.3626655720824688, "content": "静默返回旧数据（phantom data）危害：下游系统以为拿到最新有效数据；温室调控决策基于过时信息可能导致作物受损；问题隐蔽，潜伏数天才被发现；违反 API 隐含契约：成功响应代表数据完整"}`
   - 依据：该条目提供『决策基于不完整信息可能导致作物受损』这一支持依据
   - 台账里同时挂着的限制（`structured_judgement.evidence_divergence.undetermined`）：只支持『要关注』，不能证明未来一定恶化 —— 该条目是方法论提示，不是本温室的实测结论

## 5 黑盒对照（A 组平铺 vs B 组分身份）

| 指标 | A（平铺） | B（分身份） |
| --- | --- | --- |
| 回答总数 | `5` | `5` |
| 失败数 | `0` | `0` |
| 断言总数 | `0` | `0` |
| 可归因断言数（上下文口径） | `0` | `0` |
| 无来源断言数（上下文口径） | `0` | `0` |
| 可归因断言数（台账口径） | `0` | `0` |
| 回答里自己标了来源的断言数 | `0` | `0` |
| 完全没有断言的回答数 | `5` | `5` |
| 归因完整率 | `0/0` | `0/0` |
| 无来源断言率 | `0/0` | `0/0` |
| 无断言率 | `5/5` | `5/5` |
| 台账口径归因率 | `0/0` | `0/0` |
| 提到了证据来源的回答数 | `5` | `5` |
| 提到分歧/冲突的回答数 | `0` | `0` |
| 提到外部天气的回答数 | `5` | `5` |

- 数据来源：`B07/artifacts/s4_3_contrast.json`（anchor=`2026-10-21T09:30:00`，n_per_group=`5`，system_prompt_sha256=`aa1400cf8568`，question_sha256=`d3cc394bfcbe`）
- 上下文长度：A 组 `1909` 字符，B 组 `4623` 字符；共享前缀字符数 `789`（文件里就是 null）、共享块是否一致 `{'【①': True, '【②': True, '【③': True, '【⑤': True}`

**如实说明**：这张表里 A、B 两组的三个主比率**没有差异**，而且归因完整率 / 无来源断言率的分母是 0（`0/0`，比率字段为 `null`）—— 原因是 S4-3 的断言检测器词表只收「一定会 / 必然 / 必将…」这类**强断言**，两组共 0 条断言都没命中检测器词表，于是「无断言率」两组都是 1.0。

**所以：黑盒对照这一项，S4-3 留下的数据本身不足以支撑「A/B 有差别」的结论。**本轮**没有重新跑**，只能如实记录。两组真实可比的差别在别处：

- A（平铺）：回答长度 291–366 字符（均值 332.4），命中来源词的条数 10–14（均值 12.4）
- B（分身份）：回答长度 382–461 字符（均值 411.6），命中来源词的条数 13–18（均值 15.4）

## 6 文档五步逐条自评

| 文档步骤 | 本轮结论 | 证据位置 |
| --- | --- | --- |
| 第一步 找一句最终判断，反着往回查 | **通过** | 第 2 节溯源链实录：结论句 → `structured_judgement.<字段>` → `evidence_ledger.entries[i]` → 工具调用 |
| 第二步 四类信息各自的作用 | **通过** | 第 1 节对照表 + V5 的结构化字段证据表 |
| 第三步 每份证据自报身份 | **通过（有一处如实说明）** | 台账条目带 `source_type` / `source` / `time` / `status` / `result` / `error` / `direction` / `adopted` / `ext`；**没有** `role` 字段 —— 角色由 `source_type` 决定（V5 的 note 已写明） |
| 第四步 查 B05 是不是真调用 | **通过** | 第 3 节 V3/V4；`tool_calls.records` 带 `status` / `arguments` / `adopted` / `error` |
| 第五步 再说一次"为什么需要关注" | **通过** | 第 4 节四句拆解，每句挂到具体 `evidence_id` 与实际字段值 |

### V1–V6 判定表

| 检查 | run_082 | run_084 | run_057 | 判据 |
| --- | --- | --- | --- | --- |
| V1 溯源链闭合 | ✅ 通过 | ✅ 通过 | ✅ 通过 | — |
| V2 真调用 | ✅ 通过 | ✅ 通过 | ✅ 通过 | — |
| V3 红线：天气必须真调用 | ✅ 通过 | ✅ 通过 | ✅ 通过 | — |
| V4 知识必须真检索 | ✅ 通过 | ✅ 通过 | ✅ 通过 | — |
| V5 四类角色可区分 | ✅ 通过 | ✅ 通过 | ✅ 通过 | — |
| V6 台账自洽 | ✅ 通过 | ✅ 通过 | ✅ 通过 | — |

### 系统级扫描（全库 63 个带台账的 run）

上面的表只看三个 run。**"创新点 2 生效了吗"要回答的是全库**：
有没有哪条链断掉、有没有红线被踩。

| 检查 | 通过 / 总数 | 未通过的 run | 判据 |
| --- | --- | --- | --- |
| V1 溯源链闭合 | 63 / 63 | （无） | — |
| V2 真调用 | 63 / 63 | （无） | — |
| V3 红线：天气必须真调用 | 63 / 63 | （无） | — |
| V4 知识必须真检索 | 63 / 63 | （无） | — |
| V5 四类角色可区分 | 63 / 63 | （无） | — |
| V6 台账自洽 | 63 / 63 | （无） | — |

- 全库共 **63** 个 run 带 `evidence_ledger`（只有 S4/S5 阶段才建台账），本轮全部跑通：63 个；异常 0 个
- **V1 空真**（本次没挑出结论句，无实际检验对象）：run_036、run_037　← 这些是 `status=failed` 的 run：LLM 调用失败、`llm_explanation` 为空，本来就没有可溯源的回答。
- 存档里 `status != success` 的 run（失败显式留痕，未被伪装成成功）：
    - `run_036`：status=`failed`，回答长度 0
    - `run_037`：status=`failed`，回答长度 0

> V5 的判定有一处必须一起读：**降级证据（status≠ok）只能验到"角色仍可识别"，
> 不能验到"该类内容参与了判断"**。降级证据清单：

| run | 降级证据 | status | 内容字段是否可得 | direction |
| --- | --- | --- | --- | --- |
| run_084 | `['EV-B03-01']`（role=weather） | `unavailable` | **取不到** | （见 `evidence_divergence.undetermined`） |

> V1 只要求"能走到 ≥1 条 status=ok 的证据"。但**匹配有强弱之分**：
> `strong` = 数值命中或命中有区分度的特征词；`weak` = 只命中
> 「温度 / 湿度 / 模型」这类通用词。下表如实分档 —— 弱匹配**不等于**
> "这句话被这条证据支撑"，它只等于"这句话谈的是这一类东西"：

| run | claim_index | 结论句（截断） | 最强匹配强度 | chain_ok |
| --- | --- | --- | --- | --- |
| run_082 | 2 | 在过去的3小时内，环境状态被判定为不稳定。 | `strong` | True |
| run_082 | 4 | 基于B06模型（版本b06-20261020-v1）的预测，系统建议提高关注程度… | `strong` | True |
| run_082 | 8 | 综上所述，由于温度和湿度的显著波动，建议您密切关注大棚内环境的变化，以确保番茄生… | `strong` | True |
| run_084 | 3 | 基于 B06 模型（版本 b06-20261020-v1）的预测结果为 WATC… | `strong` | True |
| run_084 | 7 | 综合以上信息，建议密切关注大棚内的温湿度变化，以防止可能对番茄生长产生的不利影响… | `weak` | True |
| run_057 | 1 | 最近3小时内的环境变化被判定为不稳定，主要依据是温度波动达到4.4℃（超出阈值2… | `strong` | True |
| run_057 | 2 | B06模型（版本b06-20261020-v1）预测接下来1小时内需要关注（WA… | `strong` | True |
| run_057 | 5 | 综合来看，目前建议提高对大棚内环境的关注程度，特别是温度和湿度的变化。 | `weak` | True |

### 主 run 的逐条判定依据（依据全部来自 run.json）

```json
{
  "V1": {
    "name": "溯源链闭合：每条被选中的结论句都能走到 ≥1 条 status=ok 的证据",
    "passed": true,
    "claims_selected": 3,
    "applicable": true,
    "note": ""
  },
  "V2": {
    "name": "真调用：每条被引用到的证据都能找到对应的工具调用记录",
    "passed": true
  },
  "V3": {
    "name": "红线：回答提到天气时，run.json 里必须有天气工具的调用记录",
    "passed": true,
    "triggered": true,
    "answer_terms": [
      "天气",
      "降水",
      "外部"
    ],
    "weather_tool_calls": [
      {
        "tool_name": "B05.get_weather_forecast",
        "status": "ok",
        "arguments": {
          "location": "河北固安",
          "forecast_window": "6h",
          "note": "内部调用 now + 6h 两次，取基线点与预报点"
        },
        "adopted": true,
        "error": null
      }
    ]
  },
  "V4": {
    "name": "回答提到知识/检索/依据条目时，必须有 B04 检索调用记录",
    "passed": true,
    "triggered": false,
    "answer_terms": [],
    "knowledge_tool_calls": [
      {
        "tool_name": "B05.search_tomato_knowledge",
        "status": "ok",
        "arguments": {
          "query": "过期数据导致温室调控决策基于过时信息"
        },
        "adopted": true,
        "error": null
      }
    ]
  },
  "V5": {
    "name": "四类证据角色可区分（已发生 / 可能发生的外部条件 / 一般性依据 / 下一窗口预测）",
    "passed": true,
    "role_field_present_in_ledger": false,
    "role_determined_by": "source_type（台账无 role 字段）",
    "degraded_roles": [],
    "note": "台账条目**没有** role 字段（只有 source_type / direction / status / time / result / error / ext）。角色由 source_type 决定（结构化），作用由 result/ext 里的结构化字段判定 —— 不是靠 basis 文本猜的。",
    "rows": [
      {
        "source_type": "B02",
        "role": "history",
        "role_meaning": "已发生：窗口内的实测/模拟采样",
        "entry_count": 1,
        "evidence_ids": [
          "EV-B02-01"
        ],
        "status": "ok",
        "identity_fields": [
          {
            "field": "source_type",
            "present": true,
            "value": "B02"
          },
          {
            "field": "source",
            "present": true,
            "value": "B02/environment_samples"
          },
          {
            "field": "time",
            "present": true,
            "value": "2026-10-21T09:30:00"
          },
          {
            "field": "status",
            "present": true,
            "value": "ok"
          }
        ],
        "error_present": false,
        "content_fields": [
          {
            "field": "result.history",
            "present": true,
            "value": "[{'timestamp': '2026-10-21T07:00:00', 'temperature': 24.2, 'humidity': 92.6, 'lux': 0.0, 'source_type': 'simulated', 'si"
          },
          {
            "field": "result.current_state",
            "present": true,
            "value": "{'timestamp': '2026-10-21T09:30:00', 'temperature': 28.6, 'humidity': 66.9, 'lux': 42492.0, 'source_type': 'simulated', "
          },
          {
            "field": "result.stability_conclusion",
            "present": true,
            "value": "近 3 小时温度 24.2~28.6℃，温度波动 4.4℃ 超过阈值 2.0℃（超出 2.4）；湿度首尾变化 -25.7% 超过阈值 3.0%（超出 22.7）；湿度单步最大变化 6.2% 超过阈值 1.5%（超出 4.7）；湿度全程单调下"
          }
        ],
        "content_available": true,
        "degraded": false,
        "degraded_markers": [
          {
            "field": "ext",
            "present": true,
            "value": "stability_criteria, point_count",
            "rule": "ext 存在且至少一个字段非空"
          }
        ],
        "role_identifiable": true,
        "distinguishable": true
      },
      {
        "source_type": "B03",
        "role": "weather",
        "role_meaning": "可能发生的外部条件：预报点，不是已发生事实",
        "entry_count": 1,
        "evidence_ids": [
          "EV-B03-01"
        ],
        "status": "ok",
        "identity_fields": [
          {
            "field": "source_type",
            "present": true,
            "value": "B03"
          },
          {
            "field": "source",
            "present": true,
            "value": "B03/Open-Meteo（离线构造用例）"
          },
          {
            "field": "time",
            "present": true,
            "value": "2026-10-21T09:30:00"
          },
          {
            "field": "status",
            "present": true,
            "value": "ok"
          }
        ],
        "error_present": false,
        "content_fields": [
          {
            "field": "result.forecast_point",
            "present": true,
            "value": "{'timestamp': '2026-10-25T04:00', 'temperature': 14.0, 'humidity': 70.0, 'precipitation_probability': 10.0, 'cloud_cover"
          },
          {
            "field": "result.baseline_point",
            "present": true,
            "value": "{'timestamp': '2026-10-24T22:00', 'temperature': 15.0, 'humidity': 70.0, 'precipitation_probability': 50.0, 'cloud_cover"
          },
          {
            "field": "result.signal",
            "present": true,
            "value": "external_improving"
          },
          {
            "field": "ext.forecast_time",
            "present": true,
            "value": "2026-10-25T04:00"
          }
        ],
        "content_available": true,
        "degraded": false,
        "degraded_markers": [
          {
            "field": "ext",
            "present": true,
            "value": "provider, forecast_time, forecast_window, location, retrieved_at, case",
            "rule": "ext 存在且至少一个字段非空"
          }
        ],
        "role_identifiable": true,
        "distinguishable": true
      },
      {
        "source_type": "B04",
        "role": "knowledge",
        "role_meaning": "一般性依据：知识库切片，不是本温室实测结论",
        "entry_count": 1,
        "evidence_ids": [
          "EV-B04-01"
        ],
        "status": "ok",
        "identity_fields": [
          {
            "field": "source_type",
            "present": true,
            "value": "B04"
          },
          {
            "field": "source",
            "present": true,
            "value": "B04/knowledge_base"
          },
          {
            "field": "time",
            "present": true,
            "value": "2026-10-26T15:01:45.681881+00:00"
          },
          {
            "field": "status",
            "present": true,
            "value": "ok"
          }
        ],
        "error_present": false,
        "content_fields": [
          {
            "field": "result.citations",
            "present": true,
            "value": "[{'chunk_id': 'CH-003-02', 'source_id': 'SRC-003', 'section': '静默返回旧数据的危害', 'page': './docs/b03_weather.md', 'score': 0."
          },
          {
            "field": "result.query",
            "present": true,
            "value": "过期数据导致温室调控决策基于过时信息"
          },
          {
            "field": "ext.citation",
            "present": true,
            "value": "{'chunk_id': 'CH-003-02', 'source_id': 'SRC-003', 'section': '静默返回旧数据的危害', 'page': './docs/b03_weather.md', 'score': 0.3"
          }
        ],
        "content_available": true,
        "degraded": false,
        "degraded_markers": [
          {
            "field": "ext",
            "present": true,
            "value": "citation, retrieved, question, has_relevant, registry_source_type",
            "rule": "ext 存在且至少一个字段非空"
          }
        ],
        "role_identifiable": true,
        "distinguishable": true
      },
      {
        "source_type": "B06",
        "role": "model",
        "role_meaning": "下一窗口预测：模型输出，不是已发生事实",
        "entry_count": 1,
        "evidence_ids": [
          "EV-B06-01"
        ],
        "status": "ok",
        "identity_fields": [
          {
            "field": "source_type",
            "present": true,
            "value": "B06"
          },
          {
            "field": "source",
            "present": true,
            "value": "B06/b06-20261020-v1"
          },
          {
            "field": "time",
            "present": true,
            "value": "2026-10-21T09:30:00"
          },
          {
            "field": "status",
            "present": true,
            "value": "ok"
          }
        ],
        "error_present": false,
        "content_fields": [
          {
            "field": "result.predicted_attention",
            "present": true,
            "value": "WATCH"
          },
          {
            "field": "result.window_id",
            "present": true,
            "value": "s1-live-001"
          },
          {
            "field": "result.model_version",
            "present": true,
            "value": "b06-20261020-v1"
          },
          {
            "field": "result.confidence",
            "present": true,
            "value": "1.0"
          }
        ],
        "content_available": true,
        "degraded": false,
        "degraded_markers": [
          {
            "field": "ext",
            "present": true,
            "value": "model_version, predicted_attention, confidence, window_id, feature_count, metrics",
            "rule": "ext 存在且至少一个字段非空"
          }
        ],
        "role_identifiable": true,
        "distinguishable": true
      }
    ]
  },
  "V6": {
    "name": "台账自洽：adopted_calls + not_adopted_calls 条数 == total_calls；adopted_calls == 实际进入证据集的条目数",
    "passed": true,
    "total_calls": 5,
    "adopted_calls": 4,
    "not_adopted_calls_count": 1,
    "not_adopted_calls": [
      {
        "tool_name": "B05.get_current_environment",
        "status": "ok",
        "purpose": "取库内最新一条（仅作 latest_in_db 参考）",
        "arguments": {
          "location": "A区-3号温室"
        },
        "reason": "仅作 latest_in_db 参考，不参与本次方向判定，未升格为证据"
      }
    ],
    "adopted_entries_count": 4,
    "sum_ok": true,
    "entries_ok": true,
    "adoption_text": "本次调用了 5 个工具，其中 4 条结果被采用为证据"
  }
}
```

## 7 Limitations

- **L1** 本轮**没有新增任何 LLM 调用**，全部基于 S4 已落盘的 run 与 S4-3 的对照数据。
  报告里的每一句"实测值"都是从 `run.json` / `s4_3_contrast.json` 里读出来的，
  没有任何手抄或补写。
- **L2** 溯源器的"自动匹配"是**规则化**的（关键词 + 数值），不是语义理解；
  匹配不上的句子/证据一律如实标"未匹配"，不硬凑。
  具体地：角色由 `source_type` 决定；句子↔证据靠「该角色的特征词命中」或
  「句中数值出现在证据 result/ext 里」；证据↔工具调用靠「调用参数值出现在证据
  result/ext 文本里」且唯一最高，并列则不认领。匹配还分**强/弱**：
  `strong` = 数值命中或命中有区分度的特征词；`weak` = 只命中「温度/湿度/模型」
  这类通用词（只说明谈的是同一类东西，不等于该证据支撑了这句）。
  句子分类同样是规则的：先按**限制性短语**（`LIMITATION_CUES`）把限制陈述摘出去，
  剩下的才按「判断/建议类动词 + 证据线索 + 打分」挑结论句。
- **L3** 文档第一步要的是"教师任意指出一句"。本轮**同时**支持两种：
  ① 程序按规则挑句（含判断/建议类动词 + 带证据线索 + 打分排序）——
  这是没指定时的默认，但**结论句的选择不是穷尽的**，只是"程序认为重要"的那些；
  ② `--claim "句子原文"` / `--claim-index N` **人工指定任意一句**，
  该句**无条件**进入溯源（不受挑句规则约束）。
  本报告第 2 节用的是 ①；用 ② 复核的命令见第 8 节。
- **L4** S4-3 的 A/B 对照数据由 qwen-max 产生；与 S1–S3 的 qwen3.5-27b 结果
  **不可直接比**。而且如第 5 节所述，这组数据本身的三个主比率分母为 0，
  **不足以支撑 A/B 有差别的结论**。
- **L5** "四类证据作用不可互换"是**语义判断**，机器检查只能验证"有没有分别标注"
  （V5 验的是结构化字段在不在），**不能证明"确实不可替代"**。
- **L6** 主 run 是 `run_082`，但它的 `scenario` 字段是
  `S5`（S5 = S4 场景 + 天气降级开关的基线变体），
  不是文档字面写的 "S4"；本轮按文档指定用了 run_082 / run_084，
  两者除天气开关外同源（同一 anchor、同一 B06 窗口、同一固定问题：
  `system_prompt_sha256=aa1400cf8568`、
  `question_sha256=d3cc394bfcbe`）。
- **L7** 证据条目本身**不记 tool_name**，工具调用与证据的对应关系是本轮用参数值
  对齐**推导**出来的；推导依据（命中了哪个参数）已原样写进 trace，可逐条复核，
  但它是推导而非台账原生字段。
- **L8** `run_084` 的天气工具 `status=unavailable`：V3 的判据是"必须存在天气工具的
  调用记录"，记录存在且 error 明确，故通过；但**该 run 的天气结论没有可用值**，
  不能当作"天气证据可用"的证据。V5 在该 run 上也只验证到"角色仍可识别"
  （靠 `ext.provider` / `ext.forecast_window` / `ext.unavailable_context.signal`
  这些结构化降级标记），**该条证据的内容字段确实取不到**，
  所以"天气承担了外部条件这一作用"在这个 run 上**没有内容级证据** ——
  详见 V5 的 `degraded_roles`。这是如实暴露，不是打掩护。
- **L9** 本轮没有新增判据、没有新增阈值，也没有重新跑任何 LLM；
  所有"应该怎样"的判断（哪些句子算结论句、哪些词算某类证据的特征词、
  限制线索怎么归 key）都是本轮写定的规则，写在 `B07/innovation2.py` 顶部常量里，
  可复查、可争论。
- **L10** 规则在**系统级扫描**里被查出过两处缺陷，本轮已改，所以
  "一次写定"这句话只对**最终版本**成立，改动过程如下（都留在这里，不藏）：
  ① 限制线索表漏了 `evidence_unavailable` 这个 key，导致
  "需要注意的是，外部天气证据当前不可用…"这类**限制陈述**被当成结论句，
  进而 V1 在 5 个 run 上假失败（run_088 / run_093 等）；
  ② 曾试过用「句子与 limitation 原文的公共 4-gram」做兜底判据，
  实测**过度触发**——limitation 原文里引用了同一批判据数字
  （"温度波动 4.4℃，超过阈值 2.0℃" 就在 `time_anchor` 的限制原文里），
  文本重叠会把正常结论句一并误判成限制陈述（59 个 run 里误判 13 个），
  该做法已**整体废弃**，改回只认"带限制语义"的结构化短语。
  另外 `CLAIM_VERBS` 在文档清单（建议/需要/应当/预计/判定/不排除）之外
  补了「请 / 应 / 预测」三个同类词 —— 文档原话是"如…"，属于举例。
- **L11** 全库里有 2 个 `status=failed` 的 run
  （LLM 调用失败、`llm_explanation` 为空），它们**有台账但没有回答**，
  V1 对它们是**空真**（既无违反也无检验对象）。这类 run 不会被伪装成成功 ——
  失败状态原样留在存档里，扫描结果里也单列。

## 8 复现命令

```bash
# 全部溯源 + 重新生成报告（不联网、不调 LLM）
python -m B07.innovation2

# 只重建报告（读已落盘的 trace）
python -m B07.innovation2 --report-only

# 指定 run
python -m B07.innovation2 --run run_082

# —— 复核入口：老师任意指出一句 ——
# 1) 先看这个 run 一共几句、每句的编号
python -m B07.innovation2 --run run_082 --list-claims

# 2) 按编号指定一句，溯源链打到屏幕（不写任何文件）
python -m B07.innovation2 --run run_082 --claim-index 6 --show

# 3) 或直接用原文整句指定
python -m B07.innovation2 --run run_082   --claim "综上所述，由于温度和湿度的显著波动，建议您密切关注大棚内环境的变化，以确保番茄生长的适宜条件。" --show

# 回归测试
python -m pytest B07/tests -q

# 复算一致性（存档值 vs 从存档重算）
python -m B07.runs explain run_082
python -m B07.runs explain run_084
python -m B07.runs explain run_023
```

产物：
- `B07/artifacts/innovation2_trace.json` —— 溯源器输出（**严格按规格的固定格式**）
- `B07/artifacts/innovation2_trace_detail.json` —— 详版（判定依据、匹配信号、限制陈述）
- `B07/artifacts/innovation2_report.md` —— 本报告

> **输出编码**：Windows 控制台默认 codepage=936（GBK），而控制台是**按自己的
> codepage 解码程序吐出的字节**的 —— 两边对不上中文必成乱码。本模块启动时会
> `SetConsoleOutputCP` 把控制台 codepage 设成与所吐字节一致（默认 UTF-8/65001），
> 被重定向 / 走管道时不动控制台、直接吐 UTF-8。
> 想强制别的编码：`--stdout-encoding gbk`（或环境变量 `B07_STDOUT_ENCODING`），
> 也可以 `chcp 65001` 手动改控制台。
> 落盘的三个产物一律是 UTF-8（`write_text(encoding="utf-8")`），不受终端设置影响。
