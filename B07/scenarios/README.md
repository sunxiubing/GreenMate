# B07/scenarios —— 固定场景（B08 第一遍冻结产物 · 第二遍回放输入）

这五个文件是 **B08 基线的"固定输入"**：每个场景把**一次真实跑过**的分析
所用的输入原样固化下来，可以被反复加载、反复比对，条件不漂移。

> ★ 它们**不是**新的运行结果，**也不是**重新生成的。
> 它们是从 `B07/runs/<run_id>/run.json` 里**抽取**出来的快照
> —— 固化的是"当时那一次"的输入。重新生成会因为 LLM 采样与真实天气漂移而不同。

> ★ **第二遍新增**：这些文件现在也是 `B07/replay.py --scenario-file` 的**回放输入**
> —— 回放时按文件里的 `analysis_time` / `location` / `history` 走，
> 天气证据按文件里的**固定快照**注入，**不重新取数**。

---

## 1. 文件清单

| 文件 | 场景 | 名称 | 来源 run | 天气证据 | 证据台账 |
|---|---|---|---|---|---|
| `S1.json` | S1 | 稳态基线 | `run_113` | 无（按设计不接入） | 无 |
| `S2.json` | S2 | 渐进漂移 | `run_114` | 无（按设计不接入） | 无 |
| `S3.json` | S3 | 外部扰动 | `run_023` | 真实 API 快照（`is_constructed=false`） | 无 |
| `S4.json` | S4 | 多源证据分歧 | `run_103` | 离线夹具（`is_constructed=true`） | **有** |
| `S5.json` | S5 | 证据退化 | `run_120` | **不可用**（保留 error 原文） | **有** |

来源 run 会随文件里的 `source_run_id` 变动 —— **以文件里写的为准**，不要照抄本表。

> ★ S3 的来源从 `run_116` 换成了 `run_023`：`run_116` 的外部信号是
> `external_stable`（合成关注等级落到 `internal_only`），
> 撑不起 S3-4 的因果证明（"去掉外部条件后不再是 watch_external"）。
> `run_023` 的外部信号是 `external_deteriorating` → `watch_external`，
> 才能证明"外部天气确实参与了判定"。

---

## 2. 字段说明

```jsonc
{
  "scenario_id": "S3",
  "name": "外部扰动",
  "app_version": "B08_baseline_01",      // 冻结的语义版本号（不含 git sha，见基线文件）
  "model_version": "b06-20261020-v1",    // B06 模型版本
  "analysis_time": "2026-10-21T16:00:00",// 本次分析的锚点时间
  "location": "A区-3号温室",
  "question": "分析当前番茄生长环境状态以及接下来值得关注的变化，并说明判断依据。",
  "system_prompt_sha256": "aa1400cf8568",// ★ 绝对不许改 SYSTEM_PROMPT
  "question_sha256": "d3cc394bfcbe",     // ★ 绝对不许改固定问题

  "current_state": { ... },               // 当时的原始数值（不是本次分析的原始数值）
  "history_window": { "start": "...", "end": "...", "count": 6 },
  "history": [ ...6 个采样点... ],

  "run_scenario": "S3_with",              // ★ 回放时传给 run_analysis 的场景标签
  "evidence_ledger_enabled": false,       // ★ 回放时要不要重建证据台账

  "weather_evidence_snapshot": {
    // —— 天气的三个身份字段 ——
    "provider": "Open-Meteo",
    "provider_recorded_in_archive": false,// ← 存档里本来就有 provider 吗？
    "provider_source": "B03.weather.OPEN_METEO_BASE（代码常量）",
    "retrieved_at": "2026-10-24T23:53:51",
    "retrieved_at_recorded_in_archive": false,
    "retrieved_at_source": "...",
    "forecast_time": "2026-10-25T05:00",
    "forecast_time_source": "...",

    // —— 天气快照本体（原样保留）——
    "external_conditions": { ... },       // ★ 原样保留的 external_conditions
    "signal": "external_deteriorating",
    "available": true,
    "baseline_point": { ... },            // 实况点
    "forecast_point": { ... },            // 预报点
    "judge_method": "...",
    "basis": "...",
    "is_constructed": false,              // ★ 真快照=false；离线夹具=true
    "snapshot_taken_from_run_id": "run_023",
    "snapshot_source": "run.json.external_conditions",

    // —— 只有带台账的场景（S4/S5）才有 ——
    "ledger_b03_ext": { ... },            // 台账里 B03 那条的 ext，原样保留
    "case": "...",
    "degraded": false,
    "label": "..."
  },

  "knowledge_evidence_snapshot": { ... }, // 没有就 null
  "source_run_id": "run_023",
  "source_run_sha256": "...",             // 来源存档文件的 sha256
  "scenario_sha256": "...",               // ★ 本文件内容的指纹（见下）
  "notes": [ "..." ]                      // 抽取时的说明与缺口
}
```

### `scenario_sha256` 的算法

去掉 `scenario_sha256` 字段本身，把剩下的内容**规范化 JSON 化**
（`json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))`）
再取 sha256。用途只有一个：**机器判定"这个场景有没有被手改过"**。

### 天气身份三件套的放置规则

`provider` / `retrieved_at` / `forecast_time` **必须放在
`weather_evidence_snapshot` 顶层**，而不是只塞在 `external_conditions` 里面：

* 回放时要把它们**原样注入** `external_conditions`（B03 的身份字段不参与
  `EXTERNAL_FIELDS` 的拷贝，必须显式带过去）；
* 带台账的场景（S4/S5）里，身份是**从台账的 `ext` 派生的**
  （`weather_context_from_ledger`），不能靠外部条件里的散字段。

**档案里本来就没有的字段**（如 S3 的 `provider` / `retrieved_at`），
一律用 `*_recorded_in_archive=false` 标注，并在 `*_source` 里写清**取值出处**。

---

## 3. 怎么生成 / 怎么校验

```bash
# 生成（默认沿用文件里已记录的来源 run —— 冻结不能随时间漂移）
python -m B07.scenarios_build

# 校验：重算 sha256，并逐字段与来源存档比对
python -m B07.scenarios_build --verify

# 强制重新挑来源 run（会改变场景内容，慎用）
python -m B07.scenarios_build --refresh-source

# 手动指定来源
python -m B07.scenarios_build --source S1=run_113 --source S5=run_120
```

★ 生成过程**不重跑任何分析、不调 LLM、不调天气 API** ——
它只读 `B07/runs/` 里已经落盘的存档。

---

## 4. 怎么回放（B08 第二遍的用法）

```bash
python -m B07.replay --scenario-file B07/scenarios/S3.json
```

* 入口仍是 `B07.runs.run_analysis(...)`（**唯一**能起新 run 的地方）；
* `analysis_time` / `location` / `history` 按场景文件走；
* 天气证据按场景文件的**固定快照**注入 —— **不重新取数**；
* 带台账的场景（S4/S5）由 `B07.s4.build_ledger` 用快照**重建**台账；
* 回放产出的 run 落进 `B07/runs/`，并在 `B07/artifacts/replay_log.jsonl`
  追加一行，其中 `base_run_id` = 场景文件的 `source_run_id`。

---

## 5. 两类验证的区别（文档 p.90–91）

| | 真实链路验证 | 固定场景回放 / 回归测试 |
|---|---|---|
| 做什么 | 重新调 B03 真实天气 API | 用**已保存的快照**当输入 |
| 天气从哪来 | 实时（每次都不一样） | 存档里那一份（**不漂移**） |
| 证明什么 | 请求 / 参数 / 来源 / 时间记录 / 失败处理**都是真实工作的** | 同一个 `scenario_id` 重复验证时**主要条件一致** |
| 什么时候做 | 单独做一次链路验证 | **正式回归用这个** |
| 本目录的角色 | —— | ★ 就是它 |

场景里的 `is_constructed` / `snapshot_taken_from_run_id` 就是这条边界的标记：
`is_constructed=true` 表示那是离线构造的用例，`false` 表示那次取的是真实 API。

---

## 6. 已知缺口（诚实记录）

* **S3 的 `provider` / `retrieved_at` 不是存档原样记录的**：S1/S2/S3 当时的存档
  没有证据台账，`run.json.external_conditions` 也没记这两个字段。
  按项目既有约定（`B07/evidence_registry.py:_archived_b03_weather()`）
  补齐：provider 取 B03 代码常量、retrieved_at 取该 run 的 `created_at`，
  两处都用 `*_recorded_in_archive=false` + `*_source` 标注了出处。
* **S4 / S5 的天气是离线夹具**（`provider=Offline-Fixture`，`is_constructed=true`），
  不是真实天气。它们冻结的是"证据分歧 / 证据退化"这两种**判定形态**。
* **S5 保留的是 `unavailable` 状态与 error 原文** —— 降级场景本身就是要被固定下来的。
* **S1 / S2 不带台账**：按设计不接入外部证据，所以 S1-5 只能标
  "N/A（该场景无证据台账）"。
* 场景里的 `current_state` / `history` 是**当时那一次**的输入，
  与 B02 库当前的内容可能已经不同（B02 库还在被模拟器写入）。
