# B08 第二遍 · 正式验证报告（S1~S5）

> 生成时间：2026-10-30T21:47:07
> app_version：`B08_baseline_01`（完整形式 `B08_baseline_01@9246793e0ef2f7aebe9674757d8bb0573f544ade`）
> git：`9246793`，工作区 dirty = **true**（34 个文件）

> ★ **当前基线不是干净版本** —— 工作区有 34 个文件改动或未跟踪，基线里的提交指纹只覆盖已提交的部分。

---

## 1. 第二遍做了什么

第一遍冻结的是**输入**（基线版本号 + 五个固定场景 + 两层健康检查）；第二遍做的是**用这些固定输入真跑一遍，然后逐条判定**：

1. 按场景文件回放五次（每个场景 1 次 LLM 调用，共 5 次）：

   ```bash
   python -m B07.replay --scenario-file B07/scenarios/S1.json
   python -m B07.replay --scenario-file B07/scenarios/S2.json
   python -m B07.replay --scenario-file B07/scenarios/S3.json
   python -m B07.replay --scenario-file B07/scenarios/S4.json
   python -m B07.replay --scenario-file B07/scenarios/S5.json
   ```

   回放出来的 run 落在 `B07/runs/`，回放日志追加在 `B07/artifacts/replay_log.jsonl`（`base_run_id` = 场景文件里的 `source_run_id`）。
2. `python -m B07.b08 verify --all` 逐场景跑断言型检查与因果证明，结果写进 `B07/artifacts/b08_validation_records.jsonl`。
3. `python -m B07.b08 report` 生成本报告。

**判定分两类，本报告不许混：**

| | 断言型检查 | 因果证明（counter_evidence） |
|---|---|---|
| 问什么 | 这次运行是不是这样 | 改了**一个**输入，输出跟不跟着变 |
| 怎么判 | 读存档字段逐条比对 | 同一套纯函数重算（**不调 LLM**） |
| 覆盖 | S1-1…S5-5 | 每个场景各一条 |

---

## 2. 五场景验证记录摘要

| 场景 | run_id | 判定 | 检查通过 | 反向对照 | 关键结论 |
|---|---|---|---|---|---|
| `S1` | `run_121` | **PASS** | 5/5 | 通过 | 内部稳定、B06=NORMAL、合成关注等级 internal_only |
| `S2` | `run_122` | **PASS** | 3/3 | 通过 | 内部不稳定、末点温度与 S1 一致、合成关注等级 internal_only |
| `S3` | `run_123` | **PASS** | 4/4 | 通过 | 外部 deteriorating → 合成关注等级 watch_external |
| `S4` | `run_124` | **PASS** | 4/4 | 通过 | 证据分歧成立 → 合成关注等级 conflict_unresolved |
| `S5` | `run_125` | **PASS** | 5/5 | 通过 | 天气证据不可用 → 合成关注等级 watch_internal（主动退让） |

### 逐条检查明细


#### `S1`（run `run_121`，判定 **PASS**）

| 检查 | 断言 | 期望 | 实际 | 结果 |
|---|---|---|---|---|
| `S1-1` | 稳定性判定为『稳定』 | 稳定 | 稳定 | ✅ |
| `S1-2` | B06 预测为 NORMAL | NORMAL | NORMAL | ✅ |
| `S1-3` | 合成关注等级既不是 watch_internal 也不是 watch_external | ∉ {watch_internal, watch_external} | internal_only | ✅ |
| `S1-4` | 稳定判据未通过条数为 0，且 S1-1 / S1-2 同时成立 | 未通过条数=0 且 S1-1、S1-2 均成立 | 未通过条数=0；S1-1=True；S1-2=True | ✅ |
| `S1-5` | 证据台账四条 status 全为 ok | N/A（该场景无证据台账） | N/A（该场景无证据台账） | ✅ |
| 反向对照 | 把 S2 的 history 塞进 S1 的 run_bundle，重算 build_judgement（纯函数重算，不调 LLM） | 稳定性判定从『稳定』变成『不稳定』 | S1 原判定=稳定；换入 S2 的 6 点历史后重算=不稳定 | ✅ |

#### `S2`（run `run_122`，判定 **PASS**）

| 检查 | 断言 | 期望 | 实际 | 结果 |
|---|---|---|---|---|
| `S2-1` | 稳定性判定为『不稳定』 | 不稳定 | 不稳定 | ✅ |
| `S2-2` | 末点温度与 S1 的差 ≤ 0.5℃（保证两窗可比） | ≤ 0.5℃ | S1 末点=32.8℃；S2 末点=32.8℃；绝对差=0.0℃ | ✅ |
| `S2-3` | 过程量与 S1 至少一项不同（只从 history 序列算，不读结论） | 至少一项序列的最大差 > 0 | temperature 最大差=8.6；humidity 最大差=13.6；lux 最大差=19752.0 | ✅ |
| 反向对照 | 把 S1 的 history 塞进 S2 的 run_bundle，重算 build_judgement（纯函数重算，不调 LLM） | 稳定性判定从『不稳定』变成『稳定』 | S2 原判定=不稳定；换入 S1 的 6 点历史后重算=稳定 | ✅ |

#### `S3`（run `run_123`，判定 **PASS**）

| 检查 | 断言 | 期望 | 实际 | 结果 |
|---|---|---|---|---|
| `S3-1` | 外部条件可用，且 provider / retrieved_at / forecast_time 齐全 | available=True 且三个身份字段都非空 | available=True；provider=Open-Meteo；retrieved_at=2026-10-24T23:53:51；forecast_time=2026-10-25T05:00 | ✅ |
| `S3-2` | 外部信号属于三个合法取值 |  ∈ {external_deteriorating, external_improving, external_stable} | external_deteriorating | ✅ |
| `S3-3` | 天气证据确实进入了本次运行（llm_context 的 ⑥ 块可见） | llm_context 里能看到 ⑥ 外部天气条件块，且含本次外部信号 | ⑥ 块存在=True；上下文含信号 external_deteriorating=True | ✅ |
| `S3-4` | 因果证明：同一份 history 把 external_conditions 置 None 重算，合成关注等级不再是 watch_external | 带外部条件时是 watch_external；去掉外部条件后不是 watch_external | 带外部条件=watch_external；external_conditions=None 重算=internal_only | ✅ |
| 反向对照 | 同一份 history，把 external_conditions 置 None 重算 build_combined_attention（纯函数重算，不调 LLM） | 带天气 = watch_external；去掉天气 = 不再 watch_external | 带天气=watch_external；去掉天气=internal_only | ✅ |

#### `S4`（run `run_124`，判定 **PASS**）

| 检查 | 断言 | 期望 | 实际 | 结果 |
|---|---|---|---|---|
| `S4-1` | 证据分歧成立（has_conflict=True） | True | True | ✅ |
| `S4-2` | 合成关注等级为 conflict_unresolved | conflict_unresolved | conflict_unresolved | ✅ |
| `S4-3` | 分歧三列里 supporting 与 opposing 都非空 | supporting 非空 且 opposing 非空 | supporting=3 条，opposing=1 条 | ✅ |
| `S4-4` | LLM 解释没有把分歧说成一致（多字短语扫描，否定/免责句不算命中） | 不出现『都支持 / 一致支持 / 全部支持 / 无异议 / 没有矛盾』这类说法 | 未命中（解释里没有这类说法） | ✅ |
| 反向对照 | 反向对照：S5（证据退化场景）的证据分歧 | S5 的 has_conflict == False（没有可冲突的第二方向） | S5 的 has_conflict=False | ✅ |

#### `S5`（run `run_125`，判定 **PASS**）

| 检查 | 断言 | 期望 | 实际 | 结果 |
|---|---|---|---|---|
| `S5-1` | 台账里 B03 那条证据 status == unavailable | unavailable | unavailable | ✅ |
| `S5-2` | B02 / B06 / B04 三条仍然 ok（系统没有整体失效） | B02=ok, B06=ok, B04=ok | B02=ok，B06=ok，B04=ok | ✅ |
| `S5-3` | 合成关注等级为 watch_internal（主动退让） | watch_internal | watch_internal | ✅ |
| `S5-4` | LLM 解释没有凭空断言天气事实（多字短语 + 断言语气；免责句不算） | 不出现『天气事实 + 断言语气』的组合 | 未命中（解释里没有天气类断言） | ✅ |
| `S5-5` | combined_attention.reason 明确说明了降级 | reason 里点明外部信号不可用 / 降级 | 内部 B06 预测为 WATCH，按内部信号直接提升关注度；外部信号 external_unavailable 不改变这一结论 | ✅ |
| 反向对照 | 反向对照：S4（同场景、天气证据可用时）的 B03 证据状态 | S4 的 B03 status == 'ok' | S4 的 B03 status=ok | ✅ |

---

## 3. 五条因果证明（改了哪个输入、输出怎么跟着变）

每条证明都**只改一个输入**，用同一套纯函数重算（判定重算不调 LLM，Limitation L5）。

### S1：把 S2 的 history 塞进 S1 的 run_bundle，重算 build_judgement（纯函数重算，不调 LLM）

- **改了什么**：把 S2 的 history 塞进 S1 的 run_bundle，重算 build_judgement（纯函数重算，不调 LLM）
- **期望**：稳定性判定从『稳定』变成『不稳定』
- **实际**：S1 原判定=稳定；换入 S2 的 6 点历史后重算=不稳定
- **结论**：✅ 只改了 history 这一个输入，其余输入（S1 的 current_state / model_output）保持不变，结论跟着变 → 判定确实由历史驱动。

### S2：把 S1 的 history 塞进 S2 的 run_bundle，重算 build_judgement（纯函数重算，不调 LLM）

- **改了什么**：把 S1 的 history 塞进 S2 的 run_bundle，重算 build_judgement（纯函数重算，不调 LLM）
- **期望**：稳定性判定从『不稳定』变成『稳定』
- **实际**：S2 原判定=不稳定；换入 S1 的 6 点历史后重算=稳定
- **结论**：✅ 只改了 history 这一个输入，结论跟着变 → 判定确实由历史驱动。

### S3：同一份 history，把 external_conditions 置 None 重算 build_combined_attention（纯函数重算，不调 LLM）

- **改了什么**：同一份 history，把 external_conditions 置 None 重算 build_combined_attention（纯函数重算，不调 LLM）
- **期望**：带天气 = watch_external；去掉天气 = 不再 watch_external
- **实际**：带天气=watch_external；去掉天气=internal_only
- **结论**：✅ 只改了外部条件这一个输入，关注等级跟着变 → 外部天气确实参与了判定。

### S4：反向对照：S5（证据退化场景）的证据分歧

- **改了什么**：反向对照：S5（证据退化场景）的证据分歧
- **期望**：S5 的 has_conflict == False（没有可冲突的第二方向）
- **实际**：S5 的 has_conflict=False
- **结论**：✅ S4 与 S5 是同一场景、同一锚点，唯一区别是天气证据在不在：有 → 分歧成立；没有 → 不构成分歧。

### S5：反向对照：S4（同场景、天气证据可用时）的 B03 证据状态

- **改了什么**：反向对照：S4（同场景、天气证据可用时）的 B03 证据状态
- **期望**：S4 的 B03 status == 'ok'
- **实际**：S4 的 B03 status=ok
- **结论**：✅ S5 的 B03 status=unavailable 不是系统坏了，是这一条证据没取到；同一场景下证据可用时它就是 ok。

---

## 4. 两类验证的区分

| | (1) 真实链路验证 | (2) 固定场景回放 / 回归测试 |
|---|---|---|
| 外部天气从哪来 | 重新调 B03 真实天气 API | **已真实取得并保存的快照** |
| 天气会不会漂 | 会（真实世界时间轴） | 不会（条件被固定住） |
| 证明什么 | 请求 / 参数 / 来源 / 时间记录 / 失败处理都是真实工作的 | 同一个 scenario_id 重复验证时主要条件一致 |
| 本轮做没做 | **没做**（仍是缺口，见第 5 节） | ★ 就是本轮做的 |

> ★ 场景文件里的 `is_constructed` 与 `snapshot_taken_from_run_id` 就是这条边界的标记：`is_constructed=true` = 那是离线构造的用例；`false` = 那次取的是真实 API。
> **快照反映的是「取得那一刻」的外部事实，它不是实时天气。**

---

## 5. 已知缺口（诚实记录）

* **工作区是脏的** —— 基线里的提交指纹只覆盖已提交的部分；未提交的工作区改动不在基线里。
* **S3 的 provider / retrieved_at 不是存档原样记录的** —— S1/S2/S3 当时的存档没有证据台账，`run.json.external_conditions` 也没记这两个字段。快照里 provider 取自 B03 代码常量、retrieved_at 按项目既有约定（`B07/evidence_registry.py`）取该 run 的 created_at，两处都用 `*_recorded_in_archive=false` 标注了来源。
* **S4 / S5 的天气是离线夹具**（`provider=Offline-Fixture`、`is_constructed=true`），不是真实天气；它们固定的是「证据分歧 / 证据退化」这两种**判定形态**。
* **没有做真实链路验证** —— 本轮没有重新调过 B03 真实天气 API；S3 场景里的天气是历史真实 API 的**快照**，不是这一轮取的。
* **因果证明只覆盖纯函数部分** —— 判定重算不调 LLM，所以 LLM 措辞本身不在因果证明的覆盖范围内。
* **本轮只做第二遍** —— 同题对照 / 失败验证 / 运行追踪 / ≥10 个固定回归案例与第四遍回归报告**尚未开始**。

---

## 6. 本轮只做第二遍

这一轮**只做**「用固定场景真跑一遍 + 逐条判定 + 留下可复核的记录」。以下都**不在本轮**：

* 真实链路验证（重新调 B03 天气 API 的那一次）
* 同题对照 / 失败验证 / 运行追踪
* ≥10 个固定回归案例与第四遍回归报告
* 竞赛表达材料

### Limitations

* **L1** app_version 由 Git 提交指纹表达，**不是**写进 run.json 的字段；所以历史 run 无法追溯到它们产生时的 app_version，只能追溯到当前基线。本轮的验证记录**显式带** app_version。
* **L2** 知识资料版本用文件 sha256 代替版本号 —— 知识库没有版本管理机制。
* **L3** 固定场景的天气证据是**快照**，反映取得那一刻的外部事实；它不是实时天气。
* **L4** 场景文件是从**已有存档**抽取的 —— 固化的是「当时那一次」的输入，不是重新生成的。
* **L5** 判定重算**不调 LLM**，因果证明只覆盖纯函数部分（稳定性判定、合成关注等级、证据分歧）。
* **L6** S3 天气的 provider / retrieved_at 在部分存档中缺失，本轮按既有约定补齐并逐项标注来源（见第 5 节）。
* **L7** 本轮只做第二遍 —— ≥10 个固定回归案例与第四遍回归报告尚未开始。

