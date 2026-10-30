# B08 第三遍 · 证明三 | 一次完整的运行追踪（12 环节）

> 生成时间：2026-10-30
> 被追踪对象：**`run_082`**（S5 基线，证据齐全 —— B02 / B03 / B04 / B06 四种来源都在）
> 追踪命令：`python -m B07.b08_trace run_082`（结构化：`--json`）
> ★ 这是**薄包装**：数据全部来自 `B07/runs/run_082/run.json` ——
> **不调 LLM、不跑模型、不取天气、不检索知识**。
> 唯一允许的"算"是把存档里的 `history` 重新喂给 `judge_stability`
> —— 那是**纯函数复算**，用来证明稳定性判定不是抄来的。

---

## 一、12 环节完整输出

```text
==============================================================================
 B08 第三遍 · 运行追踪 · run_082
==============================================================================
  场景=S5  分析时刻=2026-10-21T09:30:00  位置=A区-3号温室
  存档=D:\GreenMate\B07\runs\run_082\run.json
  落盘时刻=2026-10-26T23:01:45  status=success

------------------------------------------------------------------------------
 ❶❷❸❹❺❻❼❽❾❿⓫⓬[1] 用户提出了什么问题
    来源：run.json.question / llm_meta.question_sha256
    问题      ：分析当前番茄生长环境状态以及接下来值得关注的变化，并说明判断依据。
    问题指纹  ：d3cc394bfcbe
    提示词指纹：aa1400cf8568
------------------------------------------------------------------------------
 ❶❷❸❹❺❻❼❽❾❿⓫⓬[2] 当前状态是什么
    来源：run.json.current_state
    采样时刻  ：2026-10-21T09:30:00
    温度=28.6℃  湿度=66.9%  光照=42492.0 lux
    数据来源  ：source_type=simulated  sim_scenario=flood
------------------------------------------------------------------------------
 ❶❷❸❹❺❻❼❽❾❿⓫⓬[3] 读取了哪一段历史数据
    来源：run.json.history_window + run.json.history
    窗口      ：2026-10-21T07:00:00 → 2026-10-21T09:30:00（6 点）
      · 2026-10-21T07:00:00  T=24.2℃  H=92.6%  L=0.0 lux
      · 2026-10-21T07:30:00  T=25.8℃  H=88.1%  L=980.0 lux
      · 2026-10-21T08:00:00  T=25.5℃  H=82.3%  L=12171.0 lux
      · 2026-10-21T08:30:00  T=27.7℃  H=77.5%  L=23240.0 lux
      · 2026-10-21T09:00:00  T=27.8℃  H=71.3%  L=32365.0 lux
      · 2026-10-21T09:30:00  T=28.6℃  H=66.9%  L=42492.0 lux
------------------------------------------------------------------------------
 ❶❷❸❹❺❻❼❽❾❿⓫⓬[4] 调用了哪些工具
    来源：run.json.evidence_ledger.tool_calls.records
    共调用 5 个工具：
      · B02.get_environment_history  status=ok  ✔ 采用
        参数：{'start': '2026-10-21T07:00:00', 'end': '2026-10-21T09:30:00'}
        用途：取历史窗口 6 点（结构化判断的内部依据）
      · B05.get_current_environment  status=ok  ✘ 未采用
        参数：{'location': 'A区-3号温室'}
        用途：取库内最新一条（仅作 latest_in_db 参考）
      · B06.predict_window  status=ok  ✔ 采用
        参数：{'window_id': 's1-live-001', 'model_version': 'b06-20261020-v1'}
        用途：B06 短时预测（只加载不重训）
      · B05.get_weather_forecast  status=ok  ✔ 采用
        参数：{'location': '河北固安', 'forecast_window': '6h', 'note': '内部调用 now + 6h 两次，取基线点与预报点'}
        用途：取外部条件（基线点 + 未来 6h 预报点）
      · B05.search_tomato_knowledge  status=ok  ✔ 采用
        参数：{'query': '过期数据导致温室调控决策基于过时信息'}
        用途：方法论知识检索（决策基于不完整信息的风险）
------------------------------------------------------------------------------
 ❶❷❸❹❺❻❼❽❾❿⓫⓬[5] 哪些工具结果被转换为证据记录
    来源：run.json.evidence_ledger.tool_calls(adopted) + entries + not_adopted_reasons
    本次调用了 5 个工具，其中 4 条结果被采用为证据
    采用为证据的工具：
      · B02.get_environment_history → ['EV-B02-01']（对上：是）
      · B06.predict_window → ['EV-B06-01']（对上：是）
      · B05.get_weather_forecast → ['EV-B03-01']（对上：是）
      · B05.search_tomato_knowledge → ['EV-B04-01']（对上：是）
    证据记录：
      · EV-B02-01（B02） status=ok
      · EV-B06-01（B06） status=ok
      · EV-B03-01（B03） status=ok
      · EV-B04-01（B04） status=ok
    未采用（附理由）：
      · B05.get_current_environment（status=ok） —— 仅作 latest_in_db 参考，不参与本次方向判定，未升格为证据
------------------------------------------------------------------------------
 ❶❷❸❹❺❻❼❽❾❿⓫⓬[6] 天气返回了什么
    来源：run.json.external_conditions + evidence_ledger.EV-B03
    provider    ：Offline-Fixture
    retrieved_at：2026-10-24T00:00:00+00:00
    forecast_time：2026-10-25T04:00
    signal      ：external_improving
    available   ：True   台账 status=ok
    judge_method：relative_delta
    相对变化    ：precip=-40.0%  temp=-1.0℃  cloud=-70.0%
    判定依据    ：相对基线差分（主判定）：降水概率 50.0% → 10.0%（Δ=-40.0 个百分点，阈值 ±30.0 个百分点） → external_improving；温度 Δ-1.0℃、云量 Δ-70.0%（只作描述，不参与判定）
    是否离线构造：True
------------------------------------------------------------------------------
 ❶❷❸❹❺❻❼❽❾❿⓫⓬[7] 知识检索到了什么
    来源：run.json.evidence_ledger.EV-B04
    status      ：ok
    query       ：过期数据导致温室调控决策基于过时信息
    top_k=5  命中 5 条  has_relevant=True
    主命中      ：CH-003-02 (source=SRC-003, score=0.3627)
      章节：静默返回旧数据的危害  出处：./docs/b03_weather.md
      摘要：静默返回旧数据（phantom data）危害：下游系统以为拿到最新有效数据；温室调控决策基于过时信息可能导致作物受损；问题隐蔽，潜伏数天才被发现；违反 API 隐含契约：成功响应代表数据完整
------------------------------------------------------------------------------
 ❶❷❸❹❺❻❼❽❾❿⓫⓬[8] 模型输出了什么
    来源：run.json.model_output + run.json.model_meta
    预测关注度  ：WATCH  confidence=1.000
    模型版本    ：b06-20261020-v1  窗口=s1-live-001
    DTW 距离    ：T=30.928  H=97.67  L=128373.56
    特征数=33  标签来源=rule_v1  sklearn=1.9.1
    系数 Top5：
      · {"feature": "current_lux", "coefficient": 1.114976, "direction": "推向WATCH"}
      · {"feature": "slope_lux", "coefficient": 0.742619, "direction": "推向WATCH"}
      · {"feature": "max_lux", "coefficient": -0.625809, "direction": "推向NORMAL"}
      · {"feature": "min_lux", "coefficient": -0.397984, "direction": "推向NORMAL"}
      · {"feature": "std_lux", "coefficient": -0.371169, "direction": "推向NORMAL"}
    训练指标    ：accuracy=0.745 precision=0.591 recall=0.448 f1=0.510
------------------------------------------------------------------------------
 ❶❷❸❹❺❻❼❽❾❿⓫⓬[9] 哪些证据有效
    来源：run.json.evidence_ledger.entries[*].status / direction
    共 4 条证据：ok=4  unavailable=0
    方向分布    ：supporting=3  opposing=1  undetermined=0
      · EV-B02-01（B02） status=ok direction=supporting
      · EV-B06-01（B06） status=ok direction=supporting
      · EV-B03-01（B03） status=ok direction=opposing
      · EV-B04-01（B04） status=ok direction=supporting
------------------------------------------------------------------------------
 ❶❷❸❹❺❻❼❽❾❿⓫⓬[10] 结构化判断怎样形成
    来源：run.json.structured_judgement + judge_stability(存档.history) 复算
    字段清单    ：['current_environment', 'recent_trend', 'predicted_attention', 'evidence_status', 'limitations', 'external_conditions', 'evidence_divergence']
    趋势窗口    ：2026-10-21T07:00:00 → 2026-10-21T09:30:00（3 小时 / 6 点）
    稳定判定    ：不稳定（存档值）
      近 3 小时温度 24.2~28.6℃，温度波动 4.4℃ 超过阈值 2.0℃（超出 2.4）；湿度首尾变化 -25.7% 超过阈值 3.0%（超出 22.7）；湿度单步最大变化 6.2% 超过阈值 1.5%（超出 4.7）；湿度全程单调下降（判据禁止全程单调） → 判定为不稳定（湿度持续单向变化）
        · 温度最大波动：值=4.4 阈值=2.0 通过=False  24.2~28.6℃，波动 4.4℃
        · 湿度首尾总变化：值=-25.7 阈值=3.0 通过=False  首 92.6% → 尾 66.9%，变化 -25.7%
        · 湿度单步最大变化：值=6.2 阈值=1.5 通过=False  单步最大 6.2%
        · 光照单步最大跳变：值=11191.0 阈值=15000 通过=True  单步最大 11191 lux
        · 湿度无持续单向趋势：值=全程单调下降 阈值=禁止全程单调 通过=False  全程单调下降
    B06 预测    ：WATCH
    稳定判定**复算**：None（与存档一致：False）
      ★ 这是**复算**，不是存档抄写 —— 把存档里的 history 重新喂给 judge_stability 跑一遍（纯函数，不调 LLM）
    结构化自检  ：passed=True  字段数=7/7
    合成关注    ：conflict_unresolved  （内部=WATCH, 外部=external_improving, has_conflict=True）
      理由：证据方向不一致（支持 3 条 / 相反 1 条），系统保留分歧，不做强制合并，也不做投票
------------------------------------------------------------------------------
 ❶❷❸❹❺❻❼❽❾❿⓫⓬[11] LLM 如何解释这份结构化判断
    来源：run.json.llm_explanation + run.json.llm_meta
    LLM 解释原文：
      根据当前的监测数据，2026年10月21日9:30时，大棚内的温度为28.6℃，湿度为66.9%，光照强度为42492.0 lux。这些数据来源于模拟场景，标签为“flood”。
      
      在过去的3小时内，环境状态被判定为不稳定。具体表现为：温度从24.2℃上升到28.6℃，波动幅度为4.4℃，超过了设定的阈值2.0℃；湿度从92.6%下降到66.9%，变化量为-25.7%，远超阈值3.0%；湿度单步最大变化为6.2%，也超过了阈值1.5%；并且湿度呈现全程单调下降趋势。
      
      基于B06模型（版本b06-20261020-v1）的预测，系统建议提高关注程度至WATCH级别，置信度为1.0。该模型仅适用于3小时至1小时的窗口，并且只覆盖温度、湿度和光照三项指标。
      
      需要注意的是，本次分析基于模拟数据，不涉及真实传感器读数。外部天气条件显示未来降水概率降低，但这些信息仅供方向性参考，不参与内部数值的直接比较与判断。
      
      综上所述，由于温度和湿度的显著波动，建议您密切关注大棚内环境的变化，以确保番茄生长的适宜条件。
    provider=ws-freh8bu2gap0g6so.cn-beijing.maas.aliyuncs.com  model=qwen-max  elapsed_ms=10424  content_length=456
    token 用量：prompt=3476 completion=307 total=3783
------------------------------------------------------------------------------
 ❶❷❸❹❺❻❼❽❾❿⓫⓬[12] 有哪些限制
    来源：run.json.structured_judgement.limitations
    共 4 条限制：
      · [simulated_data] 本次全部环境数据 source_type='simulated'，为模拟数据，非真实传感器读数；所有结论只在这套模拟时间轴上成立，不可外推到真实大棚。
      · [model_boundary] B06 模型（b06-20261020-v1，标签规则 rule_v1）只适用 3h → 1h 的窗口，且只覆盖温度 / 湿度 / 光照三项指标，范围取自 B02 训练数据分布；其测试集指标为 accuracy=0.745、precision=0.591、recall=0.448、f1=0.510，recall 0.45 / f1 0.51 表明对异常窗口存在明显漏报，预测为 NORMAL 不等于确认无异常。
      · [time_anchor] 本次 analysis_time = 2026-10-21T09:30:00，库内最新状态 latest_in_db = 2026-10-22T01:30:00，两者相隔 hours_gap = 16.0 小时；锚点选在更早位置的原因：库末点所在的 3 小时窗口不满足稳定判据（实测：近 3 小时温度 17.8~20.5℃（波动 2.7℃，阈值 2.0℃）；湿度首尾 +4.2%（阈值 3.0%）、单步最大 1.4%；光照单步最大 0 lux（阈值 15000）；全程单调上升 → 判定为不稳定）。因此本次判断描述的是 2026-10-21T09:30:00 时刻的状态，不是库末点时刻的状态。
      · [no_weather_no_kb] 本阶段的判断以 B02 历史 + B06 预测为主；B04 农艺知识库未接入，因此无法解释『为什么该数值对番茄意味着什么』；外部信息只作方向性参考，不参与内部数值的判定，其数值也不能与内部模拟值直接比较。
------------------------------------------------------------------------------
  自检
    本次追踪覆盖 12/12 环节；无缺失环节。
    本追踪只读存档：不调 LLM、不跑模型、不取天气、不检索知识。唯一重算的是稳定性判定（judge_stability，纯函数）。
==============================================================================
```

---

## 二、读者检验

> 读者只用这份材料，能不能回答「这次答案到底是怎么来的？」

| 问题 | 从这份材料哪里找答案 |
|---|---|
| 这次回答的是什么问题？ | 环节 ❶ —— 问题原文 + 指纹 `d3cc394bfcbe` |
| 当时大棚里是什么状态？ | 环节 ❷ —— T=28.6℃ / H=66.9% / L=42492.0 lux @ 2026-10-21T09:30，source_type=simulated |
| 结论依据的是哪一段时间？ | 环节 ❸ —— 2026-10-21T07:00 → 09:30 的 6 个采样点，逐点温度/湿度/光照 |
| 调了哪些工具？ | 环节 ❹ —— 5 个工具，参数、用途、状态 |
| 哪些工具结果没被用？ | 环节 ❺ —— `B05.get_current_environment`（只作 latest_in_db 参考），附理由 |
| 天气从哪来、是什么？ | 环节 ❻ —— provider=Offline-Fixture / retrieved_at / forecast_time / signal=external_improving / 判定依据与三个相对差分 |
| 知识库检到了什么？ | 环节 ❼ —— query、top_k=5、主命中 CH-003-02（score=0.3627）、章节与出处 |
| 模型怎么说？ | 环节 ❽ —— WATCH / conf=1.0 / model_version=b06-20261020-v1 / DTW 三项 / 系数 Top5 / 训练指标 |
| 哪条证据站在哪一边？ | 环节 ❾ —— 4 条证据的 status 与 direction（supporting 3 / opposing 1） |
| 稳定判定是怎么来的？ | 环节 ❿ —— 5 条判据逐条（值 / 阈值 / 通过 / note）+ **复算值与存档值一致** |
| LLM 那段话是谁写的、用了多少 token？ | 环节 ⓫ —— 原文 + provider/model/elapsed_ms/usage |
| 这条结论有什么限制？ | 环节 ⓬ —— 4 条限制（模拟数据 / 模型边界 / 时间锚点 / 知识与外部信息未接入） |
| 最终关注等级是多少、为什么？ | 环节 ❿ 末段 —— conflict_unresolved，附 reason |

**自检结论：本次追踪覆盖 12/12 环节；无缺失环节。**

★ 特别值得一看的两处：

1. **环节 ❿ 的"稳定判定复算"** —— 存档里写的是「不稳定」，
   把存档里的 `history` 重新喂给 `judge_stability` 重跑，得到的**还是**「不稳定」
   （`matches_archive=True`）。这说明这个判定**不是抄进存档的一段文字**，
   而是可以脱离 LLM 复算的规则。
2. **环节 ❹ 与 ❺ 的对照** —— 5 次调用只升格成 4 条证据，
   没被采用的那一条（`B05.get_current_environment`）**带着理由**。
   "用了什么、没用什么、为什么没用"三件事都在存档里。

---

## 三、这份追踪**不包含**什么（诚实记录）

* **不含 B06 模型内部的训练细节** —— 只有 `model_version`（b06-20261020-v1）、
  标签来源（rule_v1）、特征数（33）、DTW 距离、系数 Top5 和测试集指标；
  **没有**训练样本、特征计算过程、模型权重、超参搜索记录。
* **不含 B04 检索的完整 chunk 正文** —— 只有主命中 chunk 的
  `chunk_id`（CH-003-02）、`source_id`、`score`（0.3627）、章节名、出处路径和 120 字摘要；
  另外 4 条命中的完整正文、embedding、召回与重排过程都不在里面。
* **不含 LLM 的 `reasoning_content`** —— 本次运行的 `reasoning_content` 长度为 0
  （模型没有产出思维链）；存档里只有 `content`（最终那段话）、
  provider、model、elapsed_ms、token 用量与两个提示词指纹。
* **不含 B02 库的全量数据** —— 只有本次分析锚点附近的 6 个历史点、
  current_state 与 latest_in_db；库里有 51 个候选窗口，选窗逻辑只在
  `window_selection` 里留了摘要（候选数 / 白天段数 / 选中理由）。
* **不含"为什么选这个窗口"的完整推导** —— `window_selection.selected` 里只有
  结论与计数，51 个候选窗口逐个的淘汰理由没有展开。
* **不含本 run 之外的历史** —— 追踪的是**一次**运行，不含其它 run 的对照
  （对照在 [`b08_pass1_contrast.md`](b08_pass1_contrast.md)）。
