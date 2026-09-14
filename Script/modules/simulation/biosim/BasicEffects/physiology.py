"""常态生理：常驻效果（BASELINE）+ 连续量到离散量的映射。

这里的效果只声明"清醒静息下的基准"，参数都在构造时给；睡眠、运动等状态
由各自的效果用乘区或基础值声明。
"""
from __future__ import annotations

from ..Effect import Effect
from ..types import Influence, Tick
from ..types import BioState, StateVec, SleepState, GlycemiaState, HungerState, MoodState


# 读数分档：连续量 -> 三档枚举。切点在维度的 bands 里（见 StateVec.elements）。
_HUNGER_BY_BAND = (HungerState.HUNGRY, HungerState.CONTENT, HungerState.FULL)
_MOOD_BY_BAND = (MoodState.IRRITABLE, MoodState.NEUTRAL, MoodState.HAPPY)
_GLYCEMIA_BY_BAND = (GlycemiaState.LOW, GlycemiaState.NORMAL, GlycemiaState.HIGH)

# 低血糖对能量的扣减系数：只扣低血糖，且只算清醒时（睡着靠肝糖原）。
_LOW_GLYCEMIA = {GlycemiaState.LOW: 1.0, GlycemiaState.NORMAL: 0.0, GlycemiaState.HIGH: 0.0}
_AWAKE = {SleepState.AWAKE: 1.0, SleepState.LIGHT: 0.0, SleepState.DEEP: 0.0, SleepState.REM: 0.0}

# 血糖自身也分档：高了压得快，低了肝糖原顶上。
_GLUCOSE_BAND_FACTOR = {GlycemiaState.HIGH: 1.3, GlycemiaState.NORMAL: 1.0, GlycemiaState.LOW: 0.5}

# 胃排空速度：血糖高时饱得久，血糖低时掉得快。
_FULLNESS_BAND_FACTOR = {GlycemiaState.HIGH: 0.35, GlycemiaState.NORMAL: 1.0, GlycemiaState.LOW: 1.6}


def hunger_of(fullness: float) -> HungerState:
    return _HUNGER_BY_BAND[StateVec.dimension("fullness").band_of(fullness)]


def mood_of(mood: float) -> MoodState:
    return _MOOD_BY_BAND[StateVec.dimension("mood").band_of(mood)]


def glycemia_of(glucose: float) -> GlycemiaState:
    return _GLYCEMIA_BY_BAND[StateVec.dimension("glucose").band_of(glucose)]


class EnergyDynamics(Effect):
    """能量：清醒基准代谢；血糖过低时额外掉（睡着不算这笔）。"""

    def __init__(self, *, base_cost: float = 2.0, low_glycemia_cost: float = 4.0) -> None:
        self.base_cost = base_cost
        self.low_glycemia_cost = low_glycemia_cost
        self._inf = Influence()

    def influence(self, state: BioState, tick: Tick) -> Influence:
        glycemia = glycemia_of(state.current_state.glucose)
        penalty = self.low_glycemia_cost * _LOW_GLYCEMIA[glycemia] * _AWAKE[state.aspects.sleep]
        self._inf.delta.energy = -self.base_cost - penalty
        return self._inf


class GlucoseDynamics(Effect):
    """血糖：清醒基准消耗；睡眠时消耗变慢由睡眠效果用乘区声明。"""

    def __init__(self, *, decay_per_hour: float = 6.0) -> None:
        self.decay_per_hour = decay_per_hour
        self._inf = Influence()

    def influence(self, state: BioState, tick: Tick) -> Influence:
        glycemia = glycemia_of(state.current_state.glucose)
        self._inf.delta.glucose = -self.decay_per_hour * _GLUCOSE_BAND_FACTOR[glycemia]
        return self._inf


class FullnessDynamics(Effect):
    """饱腹：胃里装了多少。下降速度绑在血糖浓度上，不是固定斜率。"""

    def __init__(self, *, decay_per_hour: float = 20.0) -> None:
        self.decay_per_hour = decay_per_hour
        self._inf = Influence()

    def influence(self, state: BioState, tick: Tick) -> Influence:
        glycemia = glycemia_of(state.current_state.glucose)
        self._inf.delta.fullness = -self.decay_per_hour * _FULLNESS_BAND_FACTOR[glycemia]
        return self._inf


class StressDynamics(Effect):
    """压力：能量低、饥饿时上升，恢复时回落。"""

    def __init__(self, *, rise_rate: float = 10.0, fall_rate: float = 5.0,
                 energy_low: float = 40.0, hunger_factor: float = 0.8) -> None:
        self.rise_rate = rise_rate
        self.fall_rate = fall_rate
        self.energy_low = energy_low
        self.hunger_factor = hunger_factor
        self._inf = Influence()

    def influence(self, state: BioState, tick: Tick) -> Influence:
        energy = state.current_state.energy
        hungry = hunger_of(state.current_state.fullness) is HungerState.HUNGRY
        deficit = max(0.0, (self.energy_low - energy) / self.energy_low)
        gain = self.rise_rate * (deficit + (self.hunger_factor if hungry else 0.0))

        content = hunger_of(state.current_state.fullness) is HungerState.CONTENT
        if energy > self.energy_low and not hungry:
            loss = self.fall_rate
        elif energy > self.energy_low * 0.5 and content:
            loss = self.fall_rate * 0.5
        else:
            loss = 0.0

        stress = state.aspects.stress + (gain - loss) * tick.dt
        self._inf.aspects.stress = min(100.0, max(0.0, stress))
        return self._inf


class MoodDynamics(Effect):
    """心情：向目标值收敛，目标由压力与能量决定。"""

    def __init__(self, *, response_rate: float = 2.0, happy_stress: float = 30.0,
                 happy_energy: float = 60.0, irritable_stress: float = 70.0) -> None:
        self.response_rate = response_rate
        self.happy_stress = happy_stress
        self.happy_energy = happy_energy
        self.irritable_stress = irritable_stress
        self._inf = Influence()

    def influence(self, state: BioState, tick: Tick) -> Influence:
        energy = state.current_state.energy
        stress = state.aspects.stress
        target = 100.0 - stress
        if stress <= self.happy_stress and energy >= self.happy_energy:
            target = max(target, 85.0)
        elif stress >= self.irritable_stress:
            target = min(target, 20.0)
        target = min(100.0, max(0.0, target))
        self._inf.delta.mood = (target - state.current_state.mood) * self.response_rate
        return self._inf


# 常态：清醒静息下的基础生理变化。它不是引擎的默认行为，而是一组普通的效果插件。
# 想要不同的数字，就在模板里用构造参数写出来（见 templates.standard）。
BASELINE: tuple[type[Effect], ...] = (
    EnergyDynamics, GlucoseDynamics, FullnessDynamics, StressDynamics, MoodDynamics,
)
