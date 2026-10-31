# B08 第四遍 · 固定回归报告

> 生成时间：2026-10-31T19:52:20
> app_version：`B08_baseline_01@0cacc241c957640614558a93f189c8a85c1b2a25`
> git：`0cacc24`，dirty = **true**
> 回归集编号：`B08_regression_v1`

---

## 1. 第四遍要解决什么

★ **说明**：文档 p.99–101 的原文**不在本仓库里**（`docs/` 下只有 `b01_b03_design.md` 与 `b03_weather.md`）；本节按第四遍任务书逐条转写要求，并在第 9 节如实标注这一条。

第四遍要解决的是第二遍留下的三个洞：

1. **只读存档的断言永远发现不了「代码改坏了」。** 第二遍每条 check 的 actual 都直接取自 `run.json` 的存档字段 —— 只要存档没动，就算把 `judge_stability` / `build_combined_attention` 改错了，验证照样全绿。第四遍把 actual 换成**按存档原始字段重新算一遍**，再与存档值逐项比对。
2. **案例太少。** 第二遍只有 5 个场景各一条记录（5 个案例），文档要求 ≥10。第四遍扩到 13 个案例（同一场景、不同 run）+ 1 个负对照。
3. **跑过的结果没留下可比的东西。** 第四遍每次回归追加一行汇总（app_version / git_commit / 逐案例判定 / 负对照结果），并支持 `regression diff` 与上一次回归对比。

---

## 2. 固定回归集清单

共 **13 个案例**（文档要求 ≥10）+ **1 个负对照**。

| # | 案例 | 分组 | 场景 | run | 存档自称 | 判定 | 与冻结期望一致 |
|---|---|---|---|---|---|---|---|
| 1 | `S1_stable_a` | stable | `S1` | `run_121` | `S1` | PASS | ✅ |
| 2 | `S1_stable_b` | stable | `S1` | `run_113` | `S1` | PASS | ✅ |
| 3 | `S2_drift_a` | drift | `S2` | `run_122` | `S2` | PASS | ✅ |
| 4 | `S2_drift_b` | drift | `S2` | `run_114` | `S2` | PASS | ✅ |
| 5 | `S3_ext_stable` | external | `S3` | `run_116` | `S3_with` | PASS | ✅ |
| 6 | `S3_ext_deteriorating` | external | `S3` | `run_123` | `S3_with` | PASS | ✅ |
| 7 | `S3_no_weather` | external | `S3` | `run_115` | `S3_without` | PASS | ✅ |
| 8 | `S4_conflict_a` | conflict | `S4` | `run_124` | `S4` | PASS | ✅ |
| 9 | `S4_conflict_b` | conflict | `S4` | `run_117` | `S5` | PASS | ✅ |
| 10 | `S5_degrade_a` | degrade | `S5` | `run_125` | `S5` | PASS | ✅ |
| 11 | `S5_degrade_b` | degrade | `S5` | `run_119` | `S5` | PASS | ✅ |
| 12 | `S5_degrade_c` | degrade | `S5` | `run_127` | `S5` | PASS | ✅ |
| 13 | `S5_degrade_d` | degrade | `S5` | `run_126` | `S5` | PASS | ✅ |

按分组看：

* **stable**（2 个）：`S1_stable_a`, `S1_stable_b`
* **drift**（2 个）：`S2_drift_a`, `S2_drift_b`
* **external**（3 个）：`S3_ext_stable`, `S3_ext_deteriorating`, `S3_no_weather`
* **conflict**（2 个）：`S4_conflict_a`, `S4_conflict_b`
* **degrade**（4 个）：`S5_degrade_a`, `S5_degrade_b`, `S5_degrade_c`, `S5_degrade_d`

> ★ 13 个案例全部来自**已有存档**，回归**不新跑任何一次 LLM / 模型 / 天气**。

---

## 3. 文档六条行为的落实对照

| # | 行为 | 说明 | 对应检查 | 本遍覆盖的案例 | 逐案例结果 | 结论 |
|---|---|---|---|---|---|---|
| 1 | 稳定场景不误报 | 内部状态稳定时，判定是『稳定』、B06 预测 NORMAL、合成关注等级 internal_only | `S1-1`, `S1-2`, `S1-3`, `S1-4` | `S1_stable_a`, `S1_stable_b` | S1_stable_a 4/4、S1_stable_b 4/4 | ✅ |
| 2 | 漂移场景能发现 | 内部状态漂移时，判定是『不稳定』，且与稳定窗可比（末点温度差 ≤ 0.5℃） | `S2-1`, `S2-2`, `S2-3` | `S2_drift_a`, `S2_drift_b` | S2_drift_a 3/3、S2_drift_b 3/3 | ✅ |
| 3 | 外部条件参与判断 | 外部信号为 deteriorating 时合成关注等级抬升为 watch_external；去掉外部条件重算就不再是 watch_external | `S3-1`, `S3-2`, `S3-3`, `S3-4` | `S3_ext_stable`, `S3_ext_deteriorating`, `S3_no_weather` | S3_ext_stable 2/4、S3_ext_deteriorating 4/4、S3_no_weather 0/4 | ✅ |
| 4 | 证据分歧保留分歧 | 台账给出相反方向时分歧成立，关注等级为 conflict_unresolved，解释不得把分歧说成一致 | `S4-1`, `S4-2`, `S4-3`, `S4-4` | `S4_conflict_a`, `S4_conflict_b` | S4_conflict_a 4/4、S4_conflict_b 4/4 | ✅ |
| 5 | 证据退化主动退让 | B03 证据不可用时降级为 watch_internal，其余证据照常，解释不得凭空断言天气事实 | `S5-1`, `S5-2`, `S5-3`, `S5-4`, `S5-5` | `S5_degrade_a`, `S5_degrade_b`, `S5_degrade_c`, `S5_degrade_d` | S5_degrade_a 5/5、S5_degrade_b 5/5、S5_degrade_c 5/5、S5_degrade_d 5/5 | ✅ |
| 6 | 解释与结构化判断一致 | LLM 解释里不出现把分歧说成一致的说法（退化场景另查天气事实断言） | `L3` | （全部 13 个案例） | L3 层 13/13 通过 | ✅ |

> ★ 「期望里的失败」也是通过：对照组 `S3_no_weather` 的四条 S3 检查**本来就该全部不成立**（没有外部天气时不许报外部关注）—— 它的通过标准是「与冻结期望一致」，不是「全绿」。

---

## 4. ★ 负对照结果

负对照的做法：**在内存里**把一份真实存档（`run_121`）的第 3 帧温度 +1.0℃，其余一个字节不动，然后拿同一套代码重算 —— L1 复算必然与存档值对不上，这个案例**必须报 FAIL**。

| 项 | 值 |
|---|---|
| 案例 id | `NEG_must_fail` |
| 改坏的地方 | `history[2].temperature` |
| 改前 → 改后 | 34.0 → 35.0℃ |
| 作用范围 | 内存副本（deepcopy）（落盘=False） |
| 期望判定 | FAIL |
| 实际判定 | FAIL |
| 是否被抓到 | ✅ 是 |

L1 复算层抓到 **2 处**不一致：

* `S1-1` 稳定性判定：存档值 = `稳定`，重算值 = `不稳定` → **对不上**
* `S1-4` 稳定判据未通过条数：存档值 = `0`，重算值 = `1` → **对不上**

判定为未通过的检查：`['S1-1', 'S1-4']`

> ★ **负对照没被抓到 → 整个回归集无效**（说明它测不出「代码改坏了」）。本次负对照被正确抓到，回归集有效。

---

## 5. 三层检查的分工

| 层 | 问什么 | 输入 | 判据 |
|---|---|---|---|
| **L1 复算层** | 现在的代码还能不能把当年的结论**重算出来** | 存档的原始字段（history / external_conditions / evidence_ledger / model_output） | 重算值 == 存档值 |
| **L2 期望层** | 重算出来的结果**还对不对** | 第二遍的期望值（一字未改） | 重算值满足期望 |
| **L3 一致性层** | 结构化判断与 LLM 解释**有没有互相打架** | 存档里的解释原文 | 复用第二遍的 find_consistency_claims / find_weather_facts |

### ★ L1 为什么能测出「代码改坏了」

第二遍的 actual **来自存档**：

```
第二遍：  actual = run.json.structured_judgement.recent_trend.stability_verdict
          passed = (actual == '稳定')
          →  存档没动，actual 就没动；把 judge_stability 改错，验证照样全绿

第四遍：  actual = recompute_stability(archive, archive['history'])
          archived_value = run.json....stability_verdict
          match = (archived_value == recomputed_value)
          →  改了 judge_stability，重算值就变，与存档值对不上 → 立刻报错
```

关键差别只有一句：**第二遍比的是「存档等于它自己」，第四遍比的是「现在的代码算出来的等于存档」**。

> L1 的重算输入全部取自存档的**原始字段**：稳定性 ← `archive.history`；合成关注 ← `archive.model_output` + `archive.external_conditions`（+ 证据分歧分支）；证据分歧 ← `archive.evidence_ledger.entries`。**全程不调 LLM、不跑模型、不取天气。**

---

## 6. 正式回归记录

每次回归都会往 `B07/artifacts/b08_regression_results.jsonl` **追加一行汇总**，字段就是文档写明的七项 + 版本信息：

| 项 | 值 |
|---|---|
| app_version | `B08_baseline_01@0cacc241c957640614558a93f189c8a85c1b2a25` |
| model_version | `b06-20261020-v1` |
| verified_at | `2026-10-31T19:52:20` |
| total | 13 |
| passed | 13 |
| failed | 0 |
| run_ids | `run_121`, `run_113`, `run_122`, `run_114`, `run_116`, `run_123`, `run_115`, `run_124`, `run_117`, `run_125`, `run_119`, `run_127`, `run_126` |
| git_commit | `0cacc241c957640614558a93f189c8a85c1b2a25` |
| git_dirty | **true** |
| regression_set_id | `B08_regression_v1` |
| negative_control | 期望 FAIL / 实际 FAIL / 抓到 **True** |
| duration_ms | 312 |

### 与上一次回归的对比

* 上一次：2026-10-31T19:50:44（git `0cacc24`）
* 这一次：2026-10-31T19:52:20（git `0cacc24`）
* 判定翻转：0 条；新增：无；缺失：无

---

## 7. 版本漂移说明

| | 基线（第一遍冻结） | 现在 |
|---|---|---|
| app_version | `0cacc24` | `0cacc24` |
| 完整形式 | `B08_baseline_01@0cacc241c957640614558a93f189c8a85c1b2a25` | `B08_baseline_01@0cacc241c957640614558a93f189c8a85c1b2a25` |
| git.dirty | **true**（9 个文件） | **true** |
| 冻结时间 | 2026-10-31T14:51:07 | 现在 |

★ **第一遍冻结的基线不是干净基线**：冻结时 `git.dirty=true`、**9 个文件**有改动或未跟踪 —— 基线里的提交指纹只覆盖已提交的部分。现在 HEAD 是 `0cacc24`，工作区仍是脏的。

> 本轮**没有修改** `B07/artifacts/b08_baseline.json`（它是第一遍的冻结件，只读）；现在的 app_version 是用第一遍**同一个函数**（`b08_baseline.git_state()`）现算出来的。

---

## 8. 三个创新机制的回归覆盖情况

| 创新点 | 本回归集覆盖情况 | 覆盖它的案例 |
|---|---|---|
| **创新点 1**（连续时间认知消融） | **未覆盖**。消融实验要重跑 6 次 LLM，回归集是离线的（不调 LLM）。间接相关的是 S1/S2 组 4 个案例 —— 它们证明「连续 6 点历史 → 稳定性判定」这条链还在，但**消融本身（去掉某段时间/某个维度）没有任何案例** | `S1_stable_a`、`S1_stable_b`、`S2_drift_a`、`S2_drift_b`（间接） |
| **创新点 2**（可追溯证据的自动溯源器） | **未覆盖**。溯源器读的是 `innovation2_trace.json` 这类溯源产物，回归集只读 `run.json`。S4/S5 组 6 个案例覆盖的是「证据台账 → 分歧 / 降级 → 关注等级」这条链，**不是溯源器本身** | `S4_conflict_a`、`S4_conflict_b`、`S5_degrade_a`、`S5_degrade_b`、`S5_degrade_c`、`S5_degrade_d`（间接） |
| **创新点 3**（「有些话现在不能说」走查器 C1–C5） | **未覆盖**。L3 层用的是 `b08.find_consistency_claims` / `find_weather_facts`，**不是** `innovation3.py` 的 C1–C5 检测器；两者词表与判据都不同 | L3 层覆盖全部 14 个案例（但那是第二遍的检测器，不是创新点 3） |

★ 要补的案例（按优先级）：

1. **创新点 3 的走查器**：加一条「案例 = 一段已知违规的回答」，用 `innovation3.py` 的 C1–C5 扫一遍，断言它**必须**报出违规 —— 现在 L3 只在**合规**存档上跑，证明不了走查器有鉴别力。
2. **创新点 2 的溯源器**：加一条「案例 = 一份带溯源 id 的台账」，断言溯源器能还原出每条证据的来源（离线，不需要网络）。
3. **创新点 1 的消融**：加一条「案例 = 去掉中间 2 点的 history」，断言稳定性判定与完整窗口**不同**（消融的因果性），这一条可以离线做。

---

## 9. 已知缺口（诚实记录）

1. **L1 只覆盖结构化判断层，不判措辞。** 期望值针对的是「稳定 / 不稳定 / watch_external / conflict_unresolved」这类结构化结论，解释写得通不通、字数合不合规，本轮不判。
2. **L2 只读已有正式存档，不重新分析。** 它验证的是「现在的代码还能不能一致地重新推导历史存档」，**不是**「现在重新跑一遍会得到什么」—— 后者需要一次联网的活跑，是另一轮（实时回归）的事。
3. **L3 不重跑模型。** 合成关注等级用的 B06 模型输出**沿用存档值** —— 所以「模型被重训了」或「模型文件被换了」这一类改动，本回归集**查不出来**。
4. **负对照只覆盖一种改坏方式**（改一帧温度）。改判据阈值、改台账内容、改提示词拼装，这些**都没有**对应的负对照案例。
5. **S1 / S2 / S3 的存档没有证据台账**，相关断言是 N/A（**不是通过**）。S1-5 在这些存档上标的是「N/A（该场景无证据台账）」。
6. **13 个案例全部是 qwen-max**（`llm_meta.model`）。仓库里 qwen3.5-27b 的 26 个早期 run（run_001–run_037）**没有**进入回归集 —— 所以本回归集实际上是**单模型覆盖**，不是双模型覆盖。
7. **离线回归测不到真实外部服务的可用性。** 天气 API 挂了、LLM 挂了这些，本回归集测不到 —— 那是第三遍失败验证（`run_126`）覆盖的东西。
8. **回归集不覆盖页面逻辑。** HTML / CSS 改坏了，回归集**照样全绿** —— 页面只能靠 `B07/tests/test_views_*.py` 与人工看。
9. **文档 p.99–101 的原文不在仓库里**（`docs/` 下只有两份设计文档），第 1 节与第 3 节是按第四遍任务书转写的，不是逐字引用原文。
10. **期望清单是一次性冻结的。** 冻结时刻意做了三组交叉校验（第二遍冻结记录 / 同场景孪生案例 / S3 组语义规则），但**新出现的案例**在冻结时没有独立来源，只能靠人工核对。
11. **本轮 13 个案例覆盖的 13 条 run 的 llm_explanation 长度是 307~553 字，全部超过 SYSTEM_PROMPT 写的「≤250 字」**（最长 run_119 = 553 字）。仓库里带解释的 108 条 run 长度范围是 239~607 字（只有极少数在 250 以内）。这是**已知的偏差**，本轮**没有**改 SYSTEM_PROMPT（指纹 aa1400cf8568 不动），也没有把字数纳入回归断言。

