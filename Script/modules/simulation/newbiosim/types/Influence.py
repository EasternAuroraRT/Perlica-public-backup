from .BioState import StateVec

class Influence:
    """effect 的声明式影响：连续矢量力 + 离散/标量方面变更。"""
    __slots__ = ("delta", "aspects")

    def __init__(self, delta: StateVec | None = None, aspects: dict | None = None) -> None:
        self.delta = delta if delta is not None else StateVec()
        self.aspects = aspects if aspects is not None else {}

    def reset(self) -> None:
        self.delta.zero()
        self.aspects.clear()