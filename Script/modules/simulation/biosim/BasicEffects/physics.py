"""可挂载的效果：吃、运动、睡、醒。

参数全在构造时给：engine.add_effect(EatEffect(portion=0.7, quality=0.5))。
没有配置袋、没有注册表、没有动作名。
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
        self.fullness_gain = fullness_gain
        self.glucose_gain = glucose_gain
        self.energy_gain = energy_gain
        self.mood_gain = mood_gain
        self._left = digest_hours * portion
        self._inf = Influence()
        self._inf.instant.fullness = fullness_gain * portion
        span = max(self._left, 1e-9)
        self._inf.delta.glucose = glucose_gain * portion * quality / span
        self._inf.delta.energy = energy_gain * quality / span
        self._inf.delta.mood = mood_gain * min(portion, 0.8) / span

    def influence(self, state: BioState, tick: Tick) -> Influence:
        self._left -= tick.dt
        return self._inf

    def alive(self, state: BioState, tick: Tick) -> bool:
        return self._left > 0


class ExerciseEffect(Effect):
    """运动：持续耗能、耗血糖，切活跃相位；停下时相位还原。"""

    def __init__(self, *, intensity: float = 1.0, minutes: float = 20.0,
                 energy_rate: float = 4.0, glucose_rate: float = 12.0) -> None:
        self.intensity = intensity
        self.energy_rate = energy_rate
        self.glucose_rate = glucose_rate
        self._left = minutes / 60.0
        self._inf = Influence()
        self._inf.delta.energy = -energy_rate * intensity
        self._inf.delta.glucose = -glucose_rate * intensity
        self._inf.aspects.activity = ActivityLevel.ACTIVE

    def refusal(self, observation: EngineSlice) -> str | None:
        if observation.sleep is SleepState.AWAKE:
            return None
        return "你还躺着，现在动不了。"

    def influence(self, state: BioState, tick: Tick) -> Influence:
        self._left -= tick.dt
        return self._inf

    def alive(self, state: BioState, tick: Tick) -> bool:
        return self._left > 0

    def on_expire(self, state: BioState, tick: Tick) -> None:
        state.aspects.activity = ActivityLevel.MODERATE


class SleepEffect(Effect):
    """睡：什么时候醒由它自己按概率掷；睡着时交基础值与乘区。

    "睡多久"是涌现的：回满精力要多久，基本就睡多久 —— 恢复速率是参数，
    时长是结果，所以没有"睡够 8 小时"这种东西。
    """

    def __init__(self, *, recovery_per_hour: float = 9.0, wake_rate: float = 1.5,
                 wake_sharpness: float = 30.0, cycle_hours: float = 1.5,
                 glucose_factor: float = -0.6, rng: random.Random | None = None) -> None:
        self.recovery_per_hour = recovery_per_hour
        self.wake_rate = wake_rate
        self.wake_sharpness = wake_sharpness
        self.cycle_hours = cycle_hours
        self.glucose_factor = glucose_factor
        self._rng = rng if rng is not None else random.Random()
        self._inf = Influence()

    def refusal(self, observation: EngineSlice) -> str | None:
        if observation.sleep is SleepState.AWAKE:
            return None
        return "Already asleep."

    def _wake_chance(self, state: BioState, dt: float) -> float:
        """这一帧醒过来的概率：离回满越远，越不容易醒。

        写成"每小时醒率"（危险率）再按 dt 折算，所以改步长不改结论；
        注入固定种子的 rng 时，同一场觉可复现。
        """
        ceiling = StateVec.dimension("energy").high
        deficit = max(0.0, (ceiling - state.current_state.energy) / ceiling)
        hazard = self.wake_rate * math.exp(-self.wake_sharpness * deficit)
        return 1.0 - math.exp(-hazard * dt)

    def influence(self, state: BioState, tick: Tick) -> Influence:
        inf = self._inf
        a = state.aspects
        if a.sleep is SleepState.AWAKE:
            inf.aspects.sleep = SleepState.LIGHT
            inf.aspects.sleep_duration = 0.0
            return inf

        inf.delta.energy = self.recovery_per_hour     # 基础值：在回电
        inf.mul.glucose = self.glucose_factor         # 乘区：血糖消耗降低 60%

        slept = a.sleep_duration + tick.dt
        inf.aspects.sleep_duration = slept

        if self._rng.random() < self._wake_chance(state, tick.dt):
            inf.aspects.sleep = SleepState.AWAKE      # 时长留给 on_expire 记账
            return inf

        pos = (slept % self.cycle_hours) / self.cycle_hours
        inf.aspects.sleep = (SleepState.LIGHT if pos < 0.2
                             else SleepState.DEEP if pos < 0.6
                             else SleepState.REM)
        return inf

    def alive(self, state: BioState, tick: Tick) -> bool:
        return state.aspects.sleep is not SleepState.AWAKE

    def on_expire(self, state: BioState, tick: Tick) -> None:
        a = state.aspects
        if a.sleep_duration > 0:
            a.last_sleep_duration = a.sleep_duration
        a.sleep = SleepState.AWAKE
        a.sleep_duration = 0.0


class WakeEffect(Effect):
    """醒：挂上就清醒，一次性（挂上即到期）。"""

    def __init__(self) -> None:
        self._inf = Influence()
        self._inf.aspects.sleep = SleepState.AWAKE

    def refusal(self, observation: EngineSlice) -> str | None:
        if observation.sleep is SleepState.AWAKE:
            return "你本来就醒着。"
        return None

    def influence(self, state: BioState, tick: Tick) -> Influence:
        return self._inf

    def alive(self, state: BioState, tick: Tick) -> bool:
        return False
