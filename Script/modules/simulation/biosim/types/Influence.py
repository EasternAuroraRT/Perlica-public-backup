from .BioState import StateVec


class Influence:
    """effect 的声明式影响：连续速率 + 瞬时跳变 + 离散/标量方面变更。

    delta     连续矢量力（每模拟小时的变化率），引擎按 dt 积分；
    instant   瞬时矢量跳变，只在动作下达那一刻应用一次（例如进食立刻占住胃容量），
              引擎应用后即清零，效果自身不要指望它在后续更新里还存在；
    aspects   离散/标量方面的写入（枚举、计时器、时钟…），每次更新覆盖。
    """
    __slots__ = ("delta", "instant", "aspects")

    def __init__(self, delta: StateVec | None = None, aspects: dict | None = None,
                 instant: StateVec | None = None) -> None:
        self.delta = delta if delta is not None else StateVec()
        self.instant = instant if instant is not None else StateVec()
        self.aspects = aspects if aspects is not None else {}

    def reset(self) -> None:
        self.delta.zero()
        self.instant.zero()
        self.aspects.clear()
