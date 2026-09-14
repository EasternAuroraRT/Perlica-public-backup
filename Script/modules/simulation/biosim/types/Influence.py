from .BioState import AspectPatch, StateVec


class Influence:
    """效果交给引擎的声明：基础值 + 乘区 + 瞬时 + 方面。

    delta     基础值：每模拟小时的变化率，各效果相加；
    mul       乘区：提交的是倍率增量，单位元 0.0（0 = 无影响，-0.3 = 降低 30%），
              引擎把每个效果的 (1 + 提交值) 相乘再乘到基础值之和上；
    instant   瞬时跳变，只在效果落地那一刻应用一次，且不吃乘区；
    aspects   方面声明（部分）：只写自己关心的字段，写错字段名静态检查就会抓。
    """
    __slots__ = ("delta", "mul", "instant", "aspects")

    def __init__(self, delta: StateVec | None = None, instant: StateVec | None = None,
                 mul: StateVec | None = None) -> None:
        self.delta: StateVec = delta if delta is not None else StateVec()
        self.mul: StateVec = mul if mul is not None else StateVec()
        self.instant: StateVec = instant if instant is not None else StateVec()
        self.aspects: AspectPatch = AspectPatch()
