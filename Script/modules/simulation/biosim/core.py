"""效果插件契约：声明式影响（Influence）+ 注册表；effect 绝不触碰状态。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from .config import BioSimConfig
from .enums import ControlKind
from .types import BioState, Influence, Tick


class Effect(ABC):
    """效果插件契约：只声明影响，不触碰状态；生命周期交给引擎按 alive 回收。"""
    name: str = "effect"
    control: ControlKind = ControlKind.AUTONOMIC
    interruptible: bool = False
    persistent: bool = True

    def __init__(self, cfg: BioSimConfig, params: Any = None) -> None:
        self.cfg = cfg
        # 具体命令类型由各子类在自己的 __init__ 里声明；基类不认识它
        self.params: Any = params

    @classmethod
    def refusal(cls, state: BioState, cfg: BioSimConfig) -> str | None:
        """门控：允许则返回 None，不允许则返回原因（给程序判断、也转述给模型）。

        子类要么不覆盖（默认允许），要么写成同样签名的 classmethod。
        不要用 classmethod(lambda ...) 赋值 —— 那是属性不是方法，静态检查对不上，
        而且 lambda 里没有参数类型，改的时候也没有提示。
        """
        return None

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
