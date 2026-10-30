# B08 第一遍 · 冻结报告（文档 p.89–92）

> 生成时间：2026-10-30T20:31:04
> 本轮**只做第一遍（冻结）**。第二遍起（在线运行 / 场景验证 / 同题对照 /
> 失败验证 / 运行追踪 / 固定回放 / 竞赛表达）**尚未开始**。

---

## 1. 冻结了什么

| 冻结项 | 值 | 指纹 |
|---|---|---|
| app_version | `B08_baseline_01` | git `9246793` |
| app_version 完整形式 | `B08_baseline_01@9246793e0ef2f7aebe9674757d8bb0573f544ade` | — |
| model_version | `b06-20261020-v1` | manifest `ae9c9687bcf86c37efb6cf6c5da4c3a8a763cc018981462cc206fcc9d78a537e` |
| 模型权重 | `B06\artifacts\b06-20261020-v1.joblib` | `aadc847a99da34acc9a4df7c11c520398d28de218de0244b76834b02fcf35c2c` |
| 知识资料版本 | 见下表 | combined `168e3ee34c9d12ed1cbe1dfcb3886b62f47a37e1ea8b4d4c7e8fa7c7647c3624` |
| 固定问题 | 分析当前番茄生长环境状态以及接下来值得关注的变化，并说明判断依据。 | question `d3cc394bfcbe` |
| SYSTEM_PROMPT | （**绝对不许改**） | `aa1400cf8568` |
| 分支 / 提交 | `main` / `9246793e0ef2f7aebe9674757d8bb0573f544ade` | 工作区 dirty = **true**（24 个文件） |

### 知识资料版本

知识库**没有版本号机制**，用**文件 sha256** 代替版本：

| 文件 | 大小 | sha256 |
|---|---|---|
| `B04/SPEC.py` | 8137 B | `fc71b6b281a5d150756c4daa044e1e1378b547167f7b7e8056c21dd5fff9a7f0` |
| `docs/b01_b03_design.md` | 2092 B | `d5fa4fd1efd83b2fb95e2149efdf6fba841d6feb605b5294cf6ffab5e3d87093` |
| `docs/b03_weather.md` | 2111 B | `1d8692016bd6b019b34a9f88feb72f37aa5dcbacb62f129cc593de99f974d600` |

combined_sha256 的算法：按 path 升序，逐条把 `<path>\n<sha256>\n` 拼进同一个 sha256。

> ★ `B04/SPEC.py` 才是**运行时真正被检索的语料**（CHUNKS，TF-IDF）；
> `docs/*.md` 是被 chunk 引用为 `page` 的设计文档。三者一起记进基线。

### app_version 的构成规则（可复核）

```
app_version = 语义版本号 + Git 提交指纹
            = "B08_baseline_01" + git rev-parse HEAD
完整形式   : B08_baseline_01@9246793e0ef2f7aebe9674757d8bb0573f544ade
```

★ **app_version 不写进 run.json** —— run.json 是既有存档契约，一个字节都没改
（改了会连带影响视图层的 sha256 与统一检查 U2）。文档 p.89 明确允许
"如果使用 Git，可以记录对应提交版本"。代价见 Limitation L1。

---

## 2. 五个固定场景（交付物 `B07/scenarios/`）

| 场景 | 名称 | 来源 run | analysis_time | 天气证据快照 | 知识证据 | scenario_sha256 |
|---|---|---|---|---|---|---|
| `S1` | 稳态基线 | `run_113` | `2026-10-21T16:00:00` | 无（该场景按设计不接入外部天气） | 无 | `35cfdcd55c3f7ea2…` |
| `S2` | 渐进漂移 | `run_114` | `2026-10-20T12:00:00` | 无（该场景按设计不接入外部天气） | 无 | `ab788c16608bd71f…` |
| `S3` | 外部扰动 | `run_116` | `2026-10-21T16:00:00` | provider=Open-Meteo；is_constructed=False；available=True；forecast_time=2026-10-31T02:00 | 无 | `7b8d8d988cfcabfc…` |
| `S4` | 多源证据分歧 | `run_103` | `2026-10-21T09:30:00` | provider=Offline-Fixture；is_constructed=True；available=True；forecast_time=2026-10-25T04:00 | 5 条引用，has_relevant=True | `9e975d1099002b3b…` |
| `S5` | 证据退化 | `run_120` | `2026-10-21T09:30:00` | provider=Offline-Fixture；is_constructed=True；available=False；forecast_time=None；error=weather provider unavailable (B07_WEATHER_UNAVAILABLE=1): 模拟天气 API 故障，未发起真实天气请求 | 5 条引用，has_relevant=True | `c925d6b6e30a77f1…` |

* 生成方式：`python -m B07.scenarios_build`（**从已有存档抽取，不重跑分析、不调 LLM、不调天气 API**）
* 校验方式：`python -m B07.scenarios_build --verify`（重算 sha256 + 与存档逐字段比对）
* 格式说明：`B07/scenarios/README.md`

---

## 3. 两类验证的区分（文档 p.90）

| | (1) 真实链路验证 | (2) 固定场景回放 / 回归测试 |
|---|---|---|
| 外部天气从哪来 | **重新调 B03 真实天气 API** | **已真实取得并保存的快照** |
| 天气会不会漂 | 会（真实世界时间轴） | 不会（条件被固定住） |
| 证明什么 | 请求 / 参数 / 来源 / 时间记录 / 失败处理**都是真实工作的** | 同一个 `scenario_id` 重复验证时**主要条件一致** |
| 本轮做没做 | **本轮没做**（B08 第二遍做一次链路验证） | **本轮做了**（五个场景已固化） |
| 谁用 | 单独一次，证明链路没断 | **正式回归用这个** |

> ★ 场景文件里 `is_constructed` 与 `snapshot_taken_from_run_id` 就是这条边界的标记：
> `is_constructed=true` = 那是离线构造的用例；`false` = 那次取的是真实 API。
> **快照反映的是"取得那一刻"的外部事实，它不是实时天气。**

---

## 4. 怎么让别人启动这个版本

### 4.1 拉代码 + 装依赖

```bash
git clone <仓库地址> && cd GreenMate
python -m venv venv
./venv/Scripts/pip.exe install -r requirements.txt
```

### 4.2 起 Web 服务（视图 + 健康检查）

```bash
./venv/Scripts/python.exe -m B07.views_web --port 8010
```

> ★ **端口别用 8000** —— 那台机器上有别的服务在跑。默认 8010，占了就换 `--port 8020`。

浏览器打开 <http://127.0.0.1:8010/>（五个标签页：视图一 / 视图二 / 视图二·对比 / 视图三 / 视图四）。

### 4.3 健康检查（两层）

```bash
# 浅：只证明进程活着，永远 200，绝不碰外部服务
curl http://127.0.0.1:8010/health

# 深：逐项体检 B02 库 / B03 天气 / B04 知识 / B06 模型
curl http://127.0.0.1:8010/health/deep

# 深 + 真的调一次天气 API（默认不调）
curl "http://127.0.0.1:8010/health/deep?probe=true"
```

`/health` 预期返回：

```json
{"status":"ok","scope":"shallow","app_version":"B08_baseline_01",
 "model_version":"b06-20261020-v1","time":"<当前时间>","external_services_touched":false,
 "detail":"浅检查：只证明进程存活，未访问任何外部服务（数据库 / 天气 / 知识 / 模型均未触碰）。外部服务状态请查 /health/deep。"}
```

`/health/deep` 预期返回（结构）：

```json
{"status":"ok|error","scope":"deep","probe":false,
 "checks":{"B02_database":{"status":"ok","detail":"..."},
            "B03_weather":{"status":"ok","detail":"...","network_requested":false},
            "B04_knowledge":{"status":"ok","detail":"..."},
            "B06_model":{"status":"ok","detail":"..."}}}
```

> ★ **外部服务挂掉时 `/health` 仍然是 200** —— 页面不会无响应；
> 只有 `/health/deep` 会把 `status` 变成 `degraded` / `error` 并给出每一项的原因。
> 每一项都是 `ok | unavailable | error` 三态，失败原因原样带出，不伪装成功。

### 4.4 别人跑一次固定问题 → 拿到新 run_id

```bash
# 固定问题（**不许改**）：
#   分析当前番茄生长环境状态以及接下来值得关注的变化，并说明判断依据。
./venv/Scripts/python.exe -m B07.replay S1           # 真回放：调 LLM 1 次，落 1 个新 run
./venv/Scripts/python.exe -m B07.replay S1 --dry-run # 只打印计划，不调 LLM、不落盘
```

回放完拿到的 `run_XXX` 直接能在 `/` 页面里选（`run` 下拉框），四个视图都能看。

### 4.5 校验冻结没被破坏

```bash
./venv/Scripts/python.exe -m B07.scenarios_build --verify   # 场景没被手改
./venv/Scripts/python.exe -m pytest B07/tests -q             # 全套不变量
./venv/Scripts/python.exe -m B07.views check run_082         # 四视图统一检查 U1~U4
```

---

## 5. 冻结的已知缺口（诚实记录）

1. **★ 工作区是脏的**：`git.dirty = true`（24 个文件有改动或未跟踪）。
   基线对应的提交是 `9246793`，但**未提交的工作区改动不在这个基线里** ——
   所以它**不是一份干净基线**。提交之后再跑一次 `python -m B07.b08_baseline` 刷新快照。
2. **现有 103 个 run 是 B07 开发期产物**（带证据台账的 69 个），
   **不是 B08 正式验证记录**。B08 第二遍起的正式验证会另行归档并显式带 app_version。
3. **知识库没有版本号机制**，用文件 sha256 代替；文件一改 sha 就变，等价于版本，
   但**没有 changelog 语义**。
4. **S3 场景的 `retrieved_at` 为空**：S1/S2/S3 当时的存档没有证据台账，
   `run.json.external_conditions` 也没记 `provider` / `retrieved_at`。
   快照按存档原样保留、**没有推测补齐**；`provider` 取自 B03 代码常量，
   并用 `provider_recorded_in_archive=false` 明确标注。
5. **S4 / S5 的天气是离线夹具**（`provider=Offline-Fixture`、`is_constructed=true`），
   不是真实天气。它们固定的是"证据分歧 / 证据退化"这两种**判定形态**。
6. **本轮没有做任何真实链路验证**（没有重新调过 B03 真实天气 API），
   那是第二遍的事。

---

## 6. Limitations

* **L1** app_version 由 Git tag + commit 表达，**不是**写进 run.json 的字段；
  所以**历史 run 无法追溯到它们产生时的 app_version**，只能追溯到当前基线。
  B08 第二遍起的正式验证记录会显式带 app_version。
* **L2** 知识资料版本用文件 sha256 代替版本号 —— 因为知识库目前没有版本管理机制；
  文件一改 sha 就变，等价于版本。
* **L3** 固定场景的天气证据是**快照**，反映的是"取得那一刻"的外部事实；
  它不是实时天气。真实链路验证要单独做。
* **L4** 场景文件是从**已有存档**抽取的 —— 它固化的是"当时那一次"的输入，
  不是重新生成的。重新生成会因 LLM 采样与真实天气漂移而不同。
* **L5** 本轮只做第一遍。正式验证记录、同题对照 / 失败验证 / 运行追踪的证明材料、
  ≥10 个固定回归案例、回归报告、竞赛材料**都还没有开始**。

---

## 7. 产物清单

| 文件 | 作用 |
|---|---|
| `B07/artifacts/b08_baseline.json` | 基线版本快照（可重生成：`python -m B07.b08_baseline`） |
| `B07/artifacts/b08_freeze_report.md` | 本报告 |
| `B07/scenarios/S1.json` … `S5.json` | 五个固定场景 |
| `B07/scenarios/README.md` | 场景格式 / 生成 / 校验说明 |
| `B07/scenarios_build.py` | 场景抽取与校验器 |
| `B07/b08_baseline.py` | 基线快照生成器 |
| `B07/health.py` | 两层健康检查（浅 / 深） |
| `B07/views_web.py` | 新增 `/health`、`/health/deep` |
| `B01/main.py` | 新增 `/health`、`/health/deep`（B01 是"打开系统"的第一个入口） |
| `B07/tests/test_b08_freeze.py` | 冻结基线的不变量测试 |
