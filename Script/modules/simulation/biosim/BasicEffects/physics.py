"""可挂载的效果：吃、运动、睡、醒。

参数全在构造时给：engine.add_effect(EatEffect(portion=0.7, quality=0.5))。
没有配置袋、没有注册表、没有动作名。

全部声明式：`influence` 是**纯查询** —— 引擎问一次，它就产生一份新的声明；
没有可复用的可变缓冲，也没有收尾函数。到期那一帧的终态也在这份声明里。

单位约定：时长/剩余一律 `*_hours`；变化速率一律 `*_per_hour`（每小时）；
`*_gain` 是瞬时点数；portion/quality/intensity/factor 无量纲。
"""
from __future__ import annotations

import math
import random

from ..Effect import Effect
from ..EngineSlice import EngineSlice
from ..types import ActivityLevel, Influence, SleepState, Tick
from ..types import BioState, StateVec


class EatEffect(Effect):
    """吃：当场把胃装满（瞬时），之后血糖/能量/心情随消化窗口上升。

    饱腹的下降不归这里管 —— 那是 FullnessDynamics 按血糖浓度决定的胃排空速度。
    """

    def __init__(self, *, portion: float = 0.5, quality: float = 1.0,
                 digest_hours: float = 1.0, fullness_gain: float = 60.0,
                 glucose_gain: float = 70.0, energy_gain: float = 8.0,
                 mood_gain: float = 12.0) -> None:
        self.portion = portion
        self.quality = quality
        self.digest_hours = digest_hours
        self.total_hours = digest_hours * portion
        self.fullness_gain = fullness_gain
        self.glucose_gain = glucose_gain
        self.energy_gain = energy_gain
        self.mood_gain = mood_gain

    def influence(self, state: BioState, tick: Tick) -> Influence:
        inf = Influence()
        if tick.dt_hours <= 0.0:
            inf.aspects.digest_left_hours = self.total_hours     # 落地：声明初始进度
        else:
            inf.aspects.digest_left_hours = state.aspects.digest_left_hours - tick.dt_hours
        inf.instant.fullness = self.fullness_gain * self.portion
        span_hours = max(self.total_hours, 1e-9)
        inf.delta_per_hour.glucose = self.glucose_gain * self.portion * self.quality / span_hours
        inf.delta_per_hour.energy = self.energy_gain * self.quality / span_hours
        inf.delta_per_hour.mood = self.mood_gain * min(self.portion, 0.8) / span_hours
        return inf

    def alive(self, state: BioState, tick: Tick) -> bool:
        return state.aspects.digest_left_hours > 0


class ExerciseEffect(Effect):
    """运动：持续耗能、耗血糖，切活跃相位；停下时相位还原。"""

    def __init__(self, *, intensity: float = 1.0, minutes: float = 20.0,
                 energy_rate_per_hour: float = 4.0, glucose_rate_per_hour: float = 12.0) -> None:
        self.intensity = intensity
        self.total_hours = minutes / 60.0
        self.energy_rate_per_hour = energy_rate_per_hour
        self.glucose_rate_per_hour = glucose_rate_per_hour

    def refusal(self, observation: EngineSlice) -> str | None:
        if observation.sleep is SleepState.AWAKE:
            return None
        return "你还躺着，现在动不了。"

    def influence(self, state: BioState, tick: Tick) -> Influence:
        inf = Influence()
        if tick.dt_hours <= 0.0:
            remaining_hours = self.total_hours                  # 落地：从自己的时长开始
        else:
            remaining_hours = max(0.0, state.aspects.exercise_left_hours - tick.dt_hours)
        inf.aspects.exercise_left_hours = remaining_hours
        # 到期那一帧直接声明回静息相位 —— 终态由效果自己给，不靠收尾函数
        inf.aspects.activity = ActivityLevel.ACTIVE if remaining_hours > 0.0 else ActivityLevel.MODERATE
        inf.delta_per_hour.energy = -self.energy_rate_per_hour * self.intensity
        inf.delta_per_hour.glucose = -self.glucose_rate_per_hour * self.intensity
        return inf

    def alive(self, state: BioState, tick: Tick) -> bool:
        return state.aspects.exercise_left_hours > 0


class SleepEffect(Effect):
    """睡：什么时候醒由它自己按概率掷；睡着时交基础值与乘区。

    "睡多久"是涌现的：回满精力要多久，基本就睡多久 —— 恢复速率是参数，
    时长是结果，所以没有"睡够 8 小时"这种东西。
    """

    def __init__(self, *, recovery_per_hour: float = 9.0, wake_rate_per_hour: float = 1.5,
                 wake_sharpness: float = 30.0, cycle_hours: float = 1.5,
                 glucose_factor: float = -0.6, rng: random.Random | None = None) -> None:
        self.recovery_per_hour = recovery_per_hour
        self.wake_rate_per_hour = wake_rate_per_hour
        self.wake_sharpness = wake_sharpness
        self.cycle_hours = cycle_hours
        self.glucose_factor = glucose_factor
        self._rng = rng if rng is not None else random.Random()

    def refusal(self, observation: EngineSlice) -> str | None:
        if observation.sleep is SleepState.AWAKE:
            return None
        return "Already asleep."

    def _wake_chance(self, state: BioState, dt_hours: float) -> float:
        """这一帧醒过来的概率：离回满越远，越不容易醒。

        写成"每小时醒率"（危险率）再按 dt 折算，所以改步长不改结论；
        注入固定种子的 rng 时，同一场觉可复现。
        """
        ceiling = StateVec.dimension("energy").high
        deficit = max(0.0, (ceiling - state.current_state.energy) / ceiling)
        hazard_per_hour = self.wake_rate_per_hour * math.exp(-self.wake_sharpness * deficit)
        return 1.0 - math.exp(-hazard_per_hour * dt_hours)

    def influence(self, state: BioState, tick: Tick) -> Influence:
        inf = Influence()
        a = state.aspects
        if a.sleep is SleepState.AWAKE:
            inf.aspects.sleep = SleepState.LIGHT
            inf.aspects.sleep_duration_hours = 0.0
            return inf

        inf.delta_per_hour.energy = self.recovery_per_hour     # 基础值：在回电
        inf.mul.glucose = self.glucose_factor                  # 乘区：血糖消耗降低 60%

        slept_hours = a.sleep_duration_hours + tick.dt_hours
        if self._rng.random() < self._wake_chance(state, tick.dt_hours):
            # 醒来那一帧把睡眠记账一并声明掉
            inf.aspects.sleep = SleepState.AWAKE
            inf.aspects.sleep_duration_hours = 0.0
            inf.aspects.last_sleep_duration_hours = slept_hours
            return inf

        inf.aspects.sleep_duration_hours = slept_hours
        pos = (slept_hours % self.cycle_hours) / self.cycle_hours
        inf.aspects.sleep = (SleepState.LIGHT if pos < 0.2
                             else SleepState.DEEP if pos < 0.6
                             else SleepState.REM)
        return inf

    def alive(self, state: BioState, tick: Tick) -> bool:
        return state.aspects.sleep is not SleepState.AWAKE


class WakeEffect(Effect):
    """醒：挂上就清醒，一次性（挂上即到期）。"""

    def refusal(self, observation: EngineSlice) -> str | None:
        if observation.sleep is SleepState.AWAKE:
            return "你本来就醒着。"
        return None

    def influence(self, state: BioState, tick: Tick) -> Influence:
        inf = Influence()
        inf.aspects.sleep = SleepState.AWAKE
        return inf

    def alive(self, state: BioState, tick: Tick) -> bool:
        return False


def sleep_effect(*, seed: int | None = None, **overrides) -> SleepEffect:
    """睡一觉用的效果：种子注入在这里，不进任何全局配置。"""
    return SleepEffect(rng=random.Random(seed), **overrides)
