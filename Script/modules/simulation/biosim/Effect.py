"""效果契约：效果只声明，引擎负责整合与改状态。"""
from __future__ import annotations

from abc import ABC, abstractmethod

from .EngineSlice import EngineSlice
from .types import BioState, Influence, Tick


class Effect(ABC):
    """可被引擎直接挂载的效果插件。

    **参数在构造时给**（这是它唯一的外部依赖），之后每次被问到只拿到 (state, tick)。
    全程声明式：`influence` 只交"这一帧想让状态怎么变"，连到期那一帧的终态也由它自己
    声明；没有任何直接写 state 的入口。
    """

    @abstractmethod
    def influence(self, state: BioState, tick: Tick) -> Influence:
        """声明这一帧的贡献（基础值/乘区/瞬时/方面）。不改状态。"""
        ...

    @abstractmethod
    def alive(self, state: BioState, tick: Tick) -> bool:
        """还活着吗；False 则本帧末被摘掉（终态在最后一帧的声明里，没有额外收尾函数）。"""
        return True

    def refusal(self, observation: EngineSlice) -> str | None:
        """挂上去有没有意义；没意义就把原因说出来。问的是外面看得见的读数。"""
        return None
