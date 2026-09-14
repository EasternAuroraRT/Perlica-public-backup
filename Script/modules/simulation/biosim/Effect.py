"""效果契约：效果只声明，引擎负责整合与改状态。"""
from __future__ import annotations

from abc import ABC, abstractmethod

from .EngineSlice import EngineSlice
from .types import BioState, Influence, Tick


class Effect(ABC):
    """可被引擎直接挂载的效果插件。

    **参数在构造时给**（这是它唯一的外部依赖），之后每次被问到时只拿到
    (state, tick) —— 没有配置袋、没有引擎。所以它的全部行为都能从它自己的
    字段推出来，同一个模拟里放两个参数不同的同类效果也天经地义。
    """

    @abstractmethod
    def influence(self, state: BioState, tick: Tick) -> Influence:
        """声明这一帧的贡献（基础值/乘区/瞬时/方面）。不改状态。"""
        ...

    def alive(self, state: BioState, tick: Tick) -> bool:
        """还活着吗；False 则本帧末被摘掉。"""
        return True

    def on_expire(self, state: BioState, tick: Tick) -> None:
        """被摘掉时收尾 —— 唯一允许直接写状态的地方。"""

    def refusal(self, observation: EngineSlice) -> str | None:
        """挂上去有没有意义；没意义就把原因说出来。问的是外面看得见的读数。"""
        return None
