"""B07 第三遍 · Web 版**版面顺序**的不变量测试（离线，只读静态页面）

★ 为什么要有这个测试：
  用户实测反馈过 Web 版"分布太乱" —— 面板被排成左右两栏，
  【四】掉到左下角、【二】【三】跑到右边，读起来要来回跳。
  这里把"版面规则"钉死，防止回归：

  L1 **一律单列自上而下**：`.cols` 必须是 flex column，
     绝不能再是左右两栏的 grid。
  L2 **编号必须递增**：每个视图里【一】【二】【三】【四】的出现顺序
     必须从小到大（【附】/【一~四】这类不参与编号）。
  L3 **视图一必须严格是 一 → 二 → 三 → 四**。
  L4 **并排只发生在面板内部**：`.cards` / `.qgrid` / `.duo` / `.evgrid`
     这些是"一个面板里的并列小块"，必须存在，且必须是 grid。

跑法：`python -m pytest B07/tests -q`
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

HTML_PATH = Path(__file__).resolve().parent.parent / "static" / "views.html"

CN = {"〇": 0, "零": 0, "一": 1, "二": 2, "三": 3, "四": 4,
      "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}

LOADERS = ["loadOverview", "loadEvolution", "loadCompare", "loadEvidence", "loadView4"]


@pytest.fixture(scope="module")
def html() -> str:
    return HTML_PATH.read_text(encoding="utf-8")


def _css_rule(html: str, selector: str) -> str:
    """取出 CSS 里某个选择器的规则文本。"""
    m = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", html)
    assert m, f"CSS 里找不到规则：{selector}"
    return m.group(1)


def _loader_body(html: str, name: str) -> str:
    """取出某个 loader 函数的函数体（到下一个 loader 之前）。"""
    start = html.find("async function " + name + "(")
    assert start >= 0, f"找不到 loader：{name}"
    rest = html[start + 1:]
    ends = [rest.find("async function " + n + "(") for n in LOADERS]
    ends = [e for e in ends if e > 0]
    end = min(ends) if ends else len(rest)
    return rest[:end]


def _section_numbers(body: str) -> list[int]:
    """抽出该 loader 里【N】形式的编号；【附】、【0】以外的忽略。"""
    out = []
    for m in re.finditer(r"【([^】]{1,8})】", body):
        raw = m.group(1)
        # 只取开头的编号（【1~4】→1、【二·附】→2、附 → 跳过）
        num = ""
        for ch in raw:
            if ch in CN:
                num += ch
            elif ch.isdigit():
                num += ch
            else:
                break
        if not num:
            continue
        if len(num) == 1:
            out.append(CN.get(num, int(num) if num.isdigit() else 0))
        else:                                    # 两位数（十、十一…）
            out.append(int("".join(str(CN[c]) for c in num)))
    return out


# --------------------------------------------------------------------------
# L1 一律单列自上而下
# --------------------------------------------------------------------------

def test_cols_is_single_column_flex(html):
    rule = _css_rule(html, ".cols")
    assert "flex-direction:column" in rule.replace(" ", ""), rule
    assert "grid-template-columns" not in rule, (
        "★ `.cols` 不许再是左右两栏的 grid —— 面板必须自上而下按编号排列")


def test_col_is_single_column_flex(html):
    rule = _css_rule(html, ".col")
    assert "flex-direction:column" in rule.replace(" ", ""), rule
    assert "grid-template-columns" not in rule, rule


# --------------------------------------------------------------------------
# L2 编号必须递增
# --------------------------------------------------------------------------

@pytest.mark.parametrize("loader", LOADERS)
def test_section_numbers_are_non_decreasing(html, loader):
    nums = _section_numbers(_loader_body(html, loader))
    assert nums, f"{loader} 里没有找到任何【编号】标题"
    assert nums == sorted(nums), (
        f"★ {loader} 的面板编号顺序乱了：{nums} —— 必须自上而下从小到大")


# --------------------------------------------------------------------------
# L3 视图一严格 一 → 二 → 三 → 四
# --------------------------------------------------------------------------

def test_view1_sections_are_exactly_1_2_3_4(html):
    nums = _section_numbers(_loader_body(html, "loadOverview"))
    assert nums == [1, 2, 3, 4], nums


def test_view3_sections_are_ordered(html):
    nums = _section_numbers(_loader_body(html, "loadEvidence"))
    # 【0】证据可用度 → 【1~4】四类证据分区 → 【5】工具 → 【6】分歧
    assert nums == [0, 1, 5, 6], nums


def test_view2_compare_sections_are_1_to_6(html):
    nums = _section_numbers(_loader_body(html, "loadCompare"))
    assert nums == [1, 2, 3, 4, 5, 6], nums


# --------------------------------------------------------------------------
# L4 并排只发生在面板内部
# --------------------------------------------------------------------------

@pytest.mark.parametrize("selector", [".cards", ".qgrid", ".duo", ".evgrid"])
def test_inner_grids_exist(html, selector):
    rule = _css_rule(html, selector)
    assert "display:grid" in rule.replace(" ", ""), rule
    assert "grid-template-columns" in rule.replace(" ", ""), rule


def test_view4_keeps_two_columns_a_and_b(html):
    """视图四的 A|B 并列是规格要求的，必须保留。"""
    rule = _css_rule(html, ".diff2")
    assert "grid-template-columns:1fr1fr" in rule.replace(" ", ""), rule
