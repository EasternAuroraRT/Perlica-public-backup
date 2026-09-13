"""做了什么运动 -> 强度 的判定。

模型只说"做了什么、做了多久"；折算成强度和时长是这里的事。
"""
from __future__ import annotations

_INTENSITY: dict[str, float] = {
    "站立": 0.30, "拉伸": 0.30, "散步": 0.40, "走路": 0.40,
    "瑜伽": 0.60, "慢跑": 1.00, "骑车": 1.10, "俯卧撑": 1.20,
    "深蹲": 1.20, "健身": 1.30, "打球": 1.30, "引体向上": 1.40,
    "跑步": 1.50, "爬山": 1.50, "游泳": 1.60, "冲刺": 2.00,
}

_KEYWORDS: tuple[tuple[tuple[str, ...], float], ...] = (
    (("冲刺", "全力", "爆发", "拼命"), 2.00),
    (("游泳", "游"), 1.60),
    (("跑", "爬山", "登山"), 1.50),
    (("球",), 1.30),
    (("健身", "力量", "器械", "举", "撸铁"), 1.30),
    (("俯卧撑", "深蹲", "引体", "卷腹"), 1.20),
    (("骑", "单车", "自行车"), 1.10),
    (("瑜伽", "拉伸", "舒展", "太极", "静态"), 0.50),
    (("走", "散步", "遛", "站"), 0.40),
)

_DEFAULT = 1.0
_MIN_MINUTES = 1.0
_MAX_MINUTES = 180.0


def judge(kind: str, minutes: float = 20.0) -> tuple[float, float]:
    """把「做了什么、做了多久」判定成 (intensity, minutes)。"""
    name = (kind or "").strip()
    intensity = _INTENSITY.get(name)
    if intensity is None:
        for words, value in _KEYWORDS:
            if any(word in name for word in words):
                intensity = value
                break
    if intensity is None:
        intensity = _DEFAULT

    try:
        span = float(minutes)
    except (TypeError, ValueError):
        span = 20.0
    span = min(_MAX_MINUTES, max(_MIN_MINUTES, span))
    return intensity, span

