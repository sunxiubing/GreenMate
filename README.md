# GreenMate B01 — 大棚环境监测 + LLM 状态上下文对照

把【当前大棚状态 + 用户问题】一起发给 LLM，用**同题 A/B 对照**证明 AI 真的读到了当前状态。

> ⚠️ 所有传感器数值均为**模拟数据（SIMULATED）**，未接入任何真实硬件。

**核心验收条件**：同一个问题「当前环境怎么样？」，**只改变注入的状态**，两次回答必须明显不同，并能指出是哪些状态变化导致了判断变化。

---

## 目录结构

```
GreenMate/
├── .gitignore
├── README.md
└── B01/
    ├── main.py            # FastAPI 应用：状态定义、页面路由、A/B 对照接口
    ├── llm.py             # 提示词、状态注入、配置日志、异常翻译
    ├── check_llm.py       # 独立验证 API Key / Base URL / 模型名（不含 FastAPI）
    ├── verify_step5.py    # 故障注入验收脚本：证明失败就是明确失败
    ├── requirements.txt
    ├── .env.example       # 配置模板（复制成 .env 再填）
    ├── .env               # 真实密钥，已被 .gitignore 排除，不要提交
    └── static/
        └── index.html     # 页面：A/B 双面板、可编辑数值、Context 展开
```

---

## 环境要求

- **Python 3.12**（开发验证于 3.12.7）
- 一个 OpenAI 兼容的 LLM 服务（本项目用阿里云百炼 MaaS 工作空间）

依赖版本（见 `B01/requirements.txt`）：

```
fastapi==0.143.0
uvicorn==0.54.0
pydantic==2.14.1
openai==3.29.0
python-dotenv==1.2.4
```

---

## 快速开始

### 1. 建立虚拟环境并安装依赖

```bash
cd B01
python -m venv venv
venv\Scripts\python.exe -m pip install -r requirements.txt     # Windows
# source venv/bin/activate && pip install -r requirements.txt  # macOS / Linux
```

### 2. 配置 `.env`

复制模板再填：

```bash
cd B01
copy .env.example .env        # Windows
# cp .env.example .env        # macOS / Linux
```

填入三个值：

```
LLM_API_KEY=sk-你的密钥
LLM_BASE_URL=https://<你的工作空间>.cn-beijing.maas.aliyuncs.com/compatible-mode/v1
LLM_MODEL=qwen3.5-27b
```

书写要求（踩过坑，务必遵守）：

- **不要加引号**，不要写 `export`
- **`=` 两边不要留空格**
- 文件编码用 **UTF-8 无 BOM**

### 3. 先单独验证 LLM 配置

**这一步不能跳过。** 它不依赖 FastAPI，能把「配置问题」和「代码问题」彻底分开：

```bash
venv\Scripts\python.exe check_llm.py
```

成功会打印配置摘要（key 已打码）和模型回复。失败会给出三步排查清单。

### 4. 启动服务

```bash
venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000
```

启动后**第一屏日志就是排查问题的关键**，务必看一眼：

```
[GreenMate] LLM_MODEL    = qwen3.5-27b
[GreenMate] LLM_BASE_URL = https://ws-xxxx.cn-beijing.maas.aliyuncs.com/compatible-mode/v1
[GreenMate] LLM_API_KEY  = sk-xxxx...xxxx (len=NN)
[GreenMate] 配置在启动时读取；改完 .env 必须重启服务才会生效
INFO:     Uvicorn running on http://127.0.0.1:8000
```

> **`.env` 只在进程启动那一刻读进内存。改完 `.env` 必须重启服务，否则改了等于没改。**
> 判断有没有生效，就看上面第 3 行的 key 指纹变没变。

也可以换端口跑：`--port 8001`。

### 5. 打开页面，跑 A/B 对照

浏览器访问 **<http://127.0.0.1:8000/>**

1. 点 **「加载预设 A/B」** → State A 填 `24°C / 65% / 12000lux`，State B 填 `36°C / 35% / 22000lux`
2. （可选）直接改输入框里的数值，范围校验前后端都有
3. 点 **「运行 A/B 对照」**
4. 对比两侧的 AI Answer
5. 展开每张卡片底部的 **「本次注入的 Context」**，看这次究竟喂了什么给模型

---

## 核心链是怎么被证明的

A/B 对照是一次**控制变量实验**：

| 变量 | State A | State B |
|---|---|---|
| 用户问题 | 当前环境怎么样？ | **完全相同** |
| System Prompt | 同一份 | **完全相同** |
| 模型 / 温度 / 参数 | 相同 | **相同** |
| **注入的当前状态** | 24°C / 65% / 12000lux | 36°C / 35% / 22000lux ← **唯一变量** |

因为唯一的差异是状态，两次回答的差异**只可能**来自状态。

实测输出（`HTTP 200`）：

```
State A：当前大棚环境整体适宜。温度24.0°C、湿度65.0%、光照12000.0 lux 均在常见作物
         生长范围内，无需特殊处理。关键判断因素是三项数据均处于正常区间…

State B：当前大棚温度36.0°C，湿度35.0%，光照22000.0 lux。关键影响因素是温度和湿度。
         温度36.0°C明显偏高，超出多数作物适宜范围（通常25-30°C），建议立即开启通风
         或遮阳降温。湿度35.0%偏低，易导致作物缺水，建议适当喷雾增湿。
         光照22000.0 lux处于正常强光范围，暂无需调整。请优先处理高温和干燥问题。
```

注意 State B 的一个细节：光照从 12000 涨到 22000 lux，模型**没有**跟着说"光照偏高"，而是判断"处于正常强光范围"。
这说明它在**按数值推理**，不是在无脑复述状态——这是"真的读到了"的更强证据。

### 提示词里那个关键要求

`llm.py` 的 `SYSTEM_PROMPT` 里有一条是**必须引用具体数值**。

这不是装饰。只把状态塞进上下文、不要求引用，模型很容易忽略它，给出一段和状态无关的通用回答——
那样 Step 4 会**静默失败**：页面看起来正常，但状态根本没起作用。

> 如果状态变了回答却基本不变，**先检查 Context 有没有真的更新**（展开「本次注入的 Context」看），
> 而不是先去改 Prompt。

---

## 验收脚本

### `check_llm.py` — 配置是否可用

```bash
venv\Scripts\python.exe check_llm.py
```

独立于 FastAPI，只验证 `API Key` / `Base URL` / `模型名` 三件事。

### `verify_step5.py` — 失败是否明确

```bash
venv\Scripts\python.exe verify_step5.py
```

跑三个用例，期望输出：

```
  PASS  前置条件：配置正常时，应当调用成功
  PASS  故障注入：API Key 写错
  PASS  故障注入：Base URL 指向不通的地址

Step 5 通过：成功就是成功，失败就是明确失败。
```

**这个脚本不会碰你的 `.env`。** 它靠**环境变量注入**制造故障——`load_dotenv` 默认不覆盖已存在的环境变量，
所以先给子进程设坏值，`.env` 里那行好 key 就压不进来。可以反复跑，不会把真实密钥弄坏。

判定标准有三条，缺一条都算「伪装成功」：

1. HTTP 状态码本身是失败（不能只把 error 藏在 200 的 body 里）
2. 两侧都给出**可读的**失败原因
3. 没有任何一侧返回 answer —— 失败时**绝不允许编出回答**

---

## Step 5：主动制造错误

三种方式，从推荐到不推荐：

### 方式一：跑验收脚本（最省事，不动 `.env`）

```bash
venv\Scripts\python.exe verify_step5.py
```

### 方式二：启动时临时注入（不动 `.env`）

用**另一个端口**，避免和正在跑的服务撞车。

CMD：

```bat
cd /d D:\GreenMate\B01
set LLM_API_KEY=sk-wrong
venv\Scripts\python.exe -m uvicorn main:app --port 8010
```

PowerShell：

```powershell
cd D:\GreenMate\B01
$env:LLM_API_KEY = "sk-wrong"
venv\Scripts\python.exe -m uvicorn main:app --port 8010
```

模拟网络故障就把 `LLM_API_KEY` 换成 `LLM_BASE_URL=http://127.0.0.1:9/v1`
（`9` 是 discard 端口，保证连不上）。

之所以能压过 `.env` 里的真 key：`load_dotenv` 默认 **不覆盖已存在的环境变量**，
所以进程环境里先设好的坏值会赢。`verify_step5.py` 用的就是这个机制。

关掉这个终端窗口，环境变量就随之消失，不会污染后续运行。

### 方式三：真的改 `.env`（最贴近真实故障，但有风险）

1. 编辑 `B01/.env`，把 key 改错
2. **确认已保存**——文件大小应该跟着变（正常 242 字节 → 改成 `sk-ws-wrong` 后 138 字节）
3. **重启服务**
4. 看启动日志第 3 行指纹变化 → 页面点「运行 A/B 对照」→ 应报 401
5. 改回正确 key，**再重启一次**

**预期结果**（实测）：

```
HTTP 状态码: 502
  State A: 失败 → API Key 无效或已失效（401）—— 检查 .env 的 LLM_API_KEY，改完必须重启服务
  State B: 失败 → API Key 无效或已失效（401）—— 检查 .env 的 LLM_API_KEY，改完必须重启服务
  有任何一侧返回了 answer: False
```

页面上：状态栏红字「对照失败：2 侧调用全部失败。」，两侧回答框变红并给出原因，
Context 面板显示「模型未读取任何状态。失败原因：…」。

> 💡 改坏了怎么救：VS Code 里右键 `.env` → **Timeline**（时间线）能看到每个历史版本，可以恢复。
> 底层存在 `%APPDATA%\Code\User\History\`。

---

## 常见问题

### 改了 `.env` 但完全没生效

**两个原因，按顺序检查：**

1. **没保存**。在编辑器里改了 ≠ 存到硬盘了。看编辑器标签页：小圆点 `●` = 未保存，`×` = 已保存。
   最硬的确认方式是看**文件大小变了没**。
2. **没重启服务**。`.env` 只在进程启动时读一次，之后改文件对运行中的进程毫无影响。

判断依据：启动日志的 `LLM_API_KEY = ... (len=NN)`，重启前后比对。

### 页面打不开 / 端口被占

```
ERROR:    [Errno 10048] error while attempting to bind on address ('127.0.0.1', 8000)
```

说明 8000 已经有个进程在跑（可能是之前没关掉的）。

```bash
netstat -ano | findstr :8000            # 查出 PID
taskkill /PID <PID> /F                  # 结束它
```

**或者直接换端口**：`--port 8001`，然后访问 <http://127.0.0.1:8001/>。

> ⚠️ 排查时特别注意：如果端口上跑着一个**启动时间更早**的进程，浏览器一直在跟它说话，
> 你在 `.env` 里怎么改都不会有反应。

### `Error loading ASGI app. Could not import module "main"`

`uvicorn main:app` 里的 `main:app` 是**相对当前目录**找 `main.py`。先切目录：

```bash
cd /d D:\GreenMate\B01
```

### 报错显示成 `[object Object]`

已修复。原因是 FastAPI 的 pydantic 校验失败时返回的 `detail` 是**对象数组**而不是字符串，
直接拼进字符串就变成 `[object Object]`。

现在的做法：

- 后端改用**手写的中文范围校验**（`main.py` 的 `check_state()`），失败时 `detail` 是可读字符串
- 前端 `describeError()` 同时处理字符串 / 数组 / 对象三种形态
- 前端本地也做一遍预校验，输入框带 `min`/`max`，超范围立即标红，不用等往返

### 中文日志变成乱码

输出被重定向到管道或文件时，Windows 会退回 GBK 编码。`main.py` 顶部已加：

```python
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
```

另外 `log_config()` 里每行都带 `flush=True` —— 输出到管道时会被缓冲，不 flush 就看不到，
而"看不到"恰恰是它要解决的问题。

### 页面改了但浏览器还是旧的

HTML 响应已带 `Cache-Control: no-cache`。仍然不对就 **Ctrl+Shift+R** 强制刷新。

---

## 已知限制

诚实说明，避免误用：

1. **数据是模拟的。** 没有接入任何真实传感器，`SIMULATED` 标注在页面上到处都有。
2. **A/B 机制证明的是「模型读到了状态」，不是「模型判断正确」。**
   实测中出现过反例：输入 `15.5°C / 90% / 2000lux` 时，模型回答"光照 2000.0lux 充足"——
   这个判断是错的（2000 lux 很暗）。**机制证明了状态进入了上下文并影响了输出，但不保证农业判断本身准确。**
3. **没有做并发控制。** `/api/compare` 每次请求新建线程池，没做限流。
4. **没有持久化。** 状态存在内存里（`_current`），重启即回到预设 A。
5. **`/api/ask`（单侧提问）保留但页面没用到**，主要给脚本和后续步骤留接口。

---

## 六步开发进度

| 步骤 | 内容 | 状态 |
|---|---|---|
| 1 | 页面跑起来：最小 FastAPI + HTML | ✅ |
| 2 | 加入当前状态：temperature / humidity / light，标注 SIMULATED，可切换 | ✅ |
| 3 | 单独跑通 LLM：验证 Key / Base URL / 模型名 | ✅ `check_llm.py` |
| 4 | 状态进入上下文：同题 A/B 对照，回答引用当前数值 | ✅ |
| 5 | 主动制造错误：页面/日志明确失败，不伪装成功 | ✅ `verify_step5.py` |
| 6 | 证据与重启：README、测试、调试记录、Git | ✅ |

---

## API 一览

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/` | 页面 |
| GET | `/api/state` | 服务端当前状态 |
| GET | `/api/states` | 预设 A/B 状态 |
| GET | `/api/limits` | 各字段允许范围（前端校验用） |
| POST | `/api/state/switch` | 在 A / B 之间切换 |
| POST | `/api/ask` | 用当前状态 + 问题问一次 |
| POST | `/api/compare` | **同题 A/B 对照**（核心接口） |
| GET | `/docs` | FastAPI 自动生成的接口文档 |

### `POST /api/compare`

请求：

```json
{
  "question": "当前环境怎么样？",
  "states": [
    {"id": "A", "temperature": 24, "humidity": 65, "light": 12000},
    {"id": "B", "temperature": 36, "humidity": 35, "light": 22000}
  ]
}
```

`states` 可省略，省略则用预设 A/B。

响应：

- **两侧都成功** → `200`，每个 result 带 `answer` / `context` / `model`
- **一侧成功** → `200`，失败那侧带 `error`
- **两侧都失败** → **`502`**，两侧都带 `error`，**不返回任何 answer**

失败时之所以用 502 而不是 200：`curl -f`、监控探针、CI 断言都只看状态码，
如果全失败还是 200，它们会以为一切正常 —— 那才是真正的"伪装成功"。
