"""B07 测试的公共配置

做两件事：
  1. 把项目根目录和 B02 目录放进 sys.path（B02 内部用绝对导入）
  2. 提供一个假 LLM 客户端 fixture，让测试不依赖网络与真实密钥
"""

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
for _p in (str(_PROJECT_ROOT), str(_PROJECT_ROOT / "B02")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest

# 固定问题（S1-4 硬约束），测试里所有 LLM 断言都对着它
FIXED_QUESTION = "分析当前番茄生长环境状态以及接下来值得关注的变化，并说明判断依据。"

# 假 LLM 回答：数字全部来自真实证据（32.8℃ / 56.7% / 39842 lux / 1.3℃ / 1.5% / 4948 lux）
FAKE_ANSWER = (
    "本次分析基于模拟数据：温度32.8℃、湿度56.7%、光照39842 lux。"
    "近3小时温度波动1.3℃（阈值2.0℃），湿度首尾变化1.5%（阈值3.0%），"
    "光照单步跳变4948 lux（阈值15000），无持续单向趋势，判定环境稳定。"
    "模型预测关注等级为 NORMAL，置信度1.0。"
)


class _FakeMessage:
    def __init__(self, content: str):
        self.content = content
        self.reasoning_content = ""


class _FakeChoice:
    def __init__(self, content: str):
        self.message = _FakeMessage(content)


class _FakeResponse:
    def __init__(self, content: str, model: str):
        self.choices = [_FakeChoice(content)]
        self.model = model


class _FakeCompletions:
    def __init__(self, content: str):
        self._content = content
        self.calls: list[dict] = []

    def create(self, model=None, messages=None, **kwargs):
        self.calls.append({"model": model, "messages": messages})
        return _FakeResponse(self._content, model or "fake-model")


class _FakeChat:
    def __init__(self, content: str):
        self.completions = _FakeCompletions(content)


class _FakeClient:
    def __init__(self, content: str = FAKE_ANSWER):
        self.chat = _FakeChat(content)


class _FakeBoomCompletions:
    def create(self, model=None, messages=None, **kwargs):
        raise RuntimeError("模拟 LLM 服务不可用")


class _FakeBoomClient:
    def __init__(self):
        self.chat = type("_C", (), {"completions": _FakeBoomCompletions()})()


@pytest.fixture
def fake_llm(monkeypatch):
    """把 B01.llm.get_client 换成假客户端（不联网、不需要密钥）。"""
    import B01.llm

    client = _FakeClient()
    monkeypatch.setattr(B01.llm, "get_client", lambda: client)
    monkeypatch.setenv("LLM_MODEL", "fake-model")
    monkeypatch.setenv("LLM_BASE_URL", "http://fake.invalid/v1")
    return client


@pytest.fixture
def broken_llm(monkeypatch):
    """让 LLM 调用直接失败。"""
    import B01.llm

    monkeypatch.setattr(B01.llm, "get_client", lambda: _FakeBoomClient())
    monkeypatch.setenv("LLM_MODEL", "fake-model")
    monkeypatch.setenv("LLM_BASE_URL", "http://fake.invalid/v1")


@pytest.fixture
def tmp_runs_dir(tmp_path, monkeypatch):
    """把存档目录指到临时目录，测试不污染 B07/runs/。"""
    import B07.runs as runs

    d = tmp_path / "runs"
    d.mkdir()
    monkeypatch.setattr(runs, "RUNS_DIR", d)
    monkeypatch.setattr(runs, "INDEX_PATH", d / "index.json")
    return d
