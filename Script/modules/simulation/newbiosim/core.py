"""效果插件契约：声明式影响（Influence）+ 注册表；effect 绝不触碰状态。"""
from __future__ import annotations

from abc import ABC, abstractmethod

from .types import BioState, StateVec
from .enums import ControlKind
from .types import Tick, Influence
from .config import BioSimConfig

class Effect(ABC):
    """效果插件契约：只声明影响，不触碰状态；生命周期交给引擎按 alive 回收。"""
    name: str = "effect"
    control: ControlKind = ControlKind.AUTONOMIC
    interruptible: bool = False
    persistent: bool = True
    when: classmethod | None = None

    def __init__(self, cfg: BioSimConfig, params=None) -> None:
        self.cfg = cfg
        self.params = params

    def influence(self, state: BioState, cfg: BioSimConfig, tick: Tick) -> Influence:
        return self._compute_influence(state, cfg, tick)

    @abstractmethod
    def _compute_influence(self, state: BioState, cfg: BioSimConfig, tick: Tick) -> Influence:
        ...

    def alive(self, state: BioState, cfg: BioSimConfig, tick: Tick) -> bool:
        return True

    def on_expire(self, state: BioState, cfg: BioSimConfig, tick: Tick) -> None:
        pass


# 动作注册表：参数类型 -> 效果类
_EFFECTS: dict[type, type] = {}


def effect(params_type):
    def decorator(cls):
        _EFFECTS[params_type] = cls
        return cls
    return decorator

