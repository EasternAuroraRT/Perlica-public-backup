"""状态容器：连续维度向量 + 类型化的方面表。"""
from __future__ import annotations

from dataclasses import dataclass, fields

from .Vector import Vector, Dimension
from ..BioEnum import SleepState, ActivityLevel


class StateVec(Vector):
    """连续维度向量。每个维度把自己的范围、初值、读数档位一并声明在这里。

    bands 是"读数分档"的分界点：fullness 的 (25, 60) 就是"低于 25 算饿、到 60 算饱"。
    档位只属于这个维度，所以不放进任何配置袋。
    """
    elements = [
        Dimension("energy", initial=80.0),
        Dimension("fullness", initial=100.0, bands=(25.0, 60.0)),
        Dimension("mood", initial=50.0, bands=(30.0, 70.0)),
        Dimension("glucose", initial=55.0, bands=(38.0, 62.0)),
    ]

    def __init__(self, *,
                 energy: float = 0,
                 fullness: float = 0,
                 mood: float = 0,
                 glucose: float = 0,
                 ) -> None:
        super().__init__()
        self.energy = energy
        self.fullness = fullness
        self.mood = mood
        self.glucose = glucose

    @staticmethod
    def dimension(name: str) -> Dimension:
        """按名字取维度（拿它的范围与档位）。"""
        for element in StateVec.elements:
            if element.name == name:
                return element
        raise KeyError(name)


@dataclass
class Aspects:
    """状态里**齐全**的方面表。

    字段都没有默认值：少给一个参数就直接报错。因此读 aspects.xxx 永远安全，
    不需要 .get() 兜底，也不需要运行时检查。
    """
    sleep: SleepState
    activity: ActivityLevel
    stress: float
    clock_hour: float
    elapsed_hours: float
    sleep_duration: float
    last_sleep_duration: float
    digest_left: float
    exercise_left: float


@dataclass
class AspectPatch:
    """效果提交的**部分**方面声明：只写自己关心的字段，其余保持 None。

    合并逻辑和字段表同处一室，所以加字段时不会漏掉"合并"那一半。
    """
    sleep: SleepState | None = None
    activity: ActivityLevel | None = None
    stress: float | None = None
    clock_hour: float | None = None
    elapsed_hours: float | None = None
    sleep_duration: float | None = None
    last_sleep_duration: float | None = None
    digest_left: float | None = None
    exercise_left: float | None = None

    def apply_to(self, aspects: Aspects) -> None:
        """把声明覆盖到状态上：只动给了值的字段。"""
        if self.sleep is not None:
            aspects.sleep = self.sleep
        if self.activity is not None:
            aspects.activity = self.activity
        if self.stress is not None:
            aspects.stress = self.stress
        if self.clock_hour is not None:
            aspects.clock_hour = self.clock_hour
        if self.elapsed_hours is not None:
            aspects.elapsed_hours = self.elapsed_hours
        if self.sleep_duration is not None:
            aspects.sleep_duration = self.sleep_duration
        if self.last_sleep_duration is not None:
            aspects.last_sleep_duration = self.last_sleep_duration
        if self.digest_left is not None:
            aspects.digest_left = self.digest_left
        if self.exercise_left is not None:
            aspects.exercise_left = self.exercise_left


# 两张表的字段必须一一对应；只改一边，这里立刻炸。
if {f.name for f in fields(Aspects)} != {f.name for f in fields(AspectPatch)}:
    raise RuntimeError("Aspects 与 AspectPatch 的字段必须一一对应")


class BioState:
    """一次模拟的全部状态：连续维度（向量）+ 离散/标量（方面表）。"""

    current_state: StateVec
    aspects: Aspects

    def __init__(self, *, initial_state: StateVec, aspects: Aspects) -> None:
        self.current_state = initial_state
        self.aspects = aspects
