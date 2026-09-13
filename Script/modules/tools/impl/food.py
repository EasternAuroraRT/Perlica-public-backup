"""吃了什么 -> 数值 的判定。

模型只说"吃了什么、吃了多少"；进到身体里折算成多少，是这里的事。
产出 biosim 的 EatParams 需要的两个量：
    portion  份量（相对量，决定饱腹涨幅与消化时长）
    quality  营养质量（决定回能）
"""
from __future__ import annotations

# 具体食物：(portion, quality)
_ITEMS: dict[str, tuple[float, float]] = {
    "小蛋糕": (0.35, 0.50),
    "面包": (0.45, 0.60),
    "饼干": (0.20, 0.40),
    "巧克力": (0.15, 0.50),
    "米饭": (0.60, 0.80),
    "面条": (0.60, 0.75),
    "粥": (0.40, 0.60),
    "肉": (0.50, 0.90),
    "鸡蛋": (0.20, 0.80),
    "苹果": (0.30, 0.70),
    "水果": (0.30, 0.70),
    "蔬菜": (0.30, 0.60),
    "水": (0.10, 0.20),
    "茶": (0.10, 0.20),
    "咖啡": (0.10, 0.40),
    "零食": (0.20, 0.35),
    "营养液": (0.50, 0.90),
}

# 类别关键词：先命中先用
_KEYWORDS: tuple[tuple[tuple[str, ...], tuple[float, float]], ...] = (
    (("蛋糕", "甜点", "糖", "巧克力", "冰淇淋", "布丁"), (0.30, 0.45)),
    (("饭", "面", "粥", "馒头", "面包", "主食", "粉"), (0.60, 0.75)),
    (("肉", "鱼", "鸡", "蛋", "排"), (0.50, 0.90)),
    (("水果", "果"), (0.30, 0.70)),
    (("菜", "沙拉", "蔬"), (0.30, 0.60)),
    (("水", "茶", "咖啡", "饮料", "奶"), (0.10, 0.30)),
)

_DEFAULT = (0.40, 0.60)
_MAX_PORTION = 1.5


def judge(food: str, amount: float = 1.0) -> tuple[float, float]:
    """把「吃了什么、吃了多少」判定成 (portion, quality)。"""
    name = (food or "").strip()
    base = _ITEMS.get(name)
    if base is None:
        for words, value in _KEYWORDS:
            if any(word in name for word in words):
                base = value
                break
    if base is None:
        base = _DEFAULT

    try:
        count = float(amount)
    except (TypeError, ValueError):
        count = 1.0
    count = max(0.0, count)
    return min(_MAX_PORTION, base[0] * count), base[1]

