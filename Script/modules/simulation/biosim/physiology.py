"""常驻生理效果：每个连续维度自述变化率，查表而非 if 链。"""
from __future__ import annotations

from .config import BioSimConfig
from .types import Influence, Tick
from .types import SleepState, ActivityLevel, GlycemiaState, HungerState, MoodState
from .enums import ControlKind
from .core import Effect

_ENERGY_RATE = {
    SleepState.AWAKE: lambda c: -c.energy_awake_cost,
    SleepState.DOZING: lambda c: c.energy_doze_recover,
    SleepState.LIGHT: lambda c: c.energy_sleep_recover,
    SleepState.DEEP: lambda c: c.energy_sleep_recover,
    SleepState.REM: lambda c: c.energy_sleep_recover,
}

# 血糖低时额外掉能量：饿着更累
_ENERGY_GLYCEMIA = {
    GlycemiaState.HIGH: lambda c: 0.0,
    GlycemiaState.NORMAL: lambda c: 0.0,
    GlycemiaState.LOW: lambda c: -c.energy_low_glycemia_cost,
}

# 血糖消耗：清醒时快，睡眠时慢
_GLUCOSE_RATE = {
    SleepState.AWAKE: lambda c: -c.glucose_decay_per_hour,
    SleepState.DOZING: lambda c: -c.glucose_decay_per_hour * c.glucose_sleep_decay_factor,
    SleepState.LIGHT: lambda c: -c.glucose_decay_per_hour * c.glucose_sleep_decay_factor,
    SleepState.DEEP: lambda c: -c.glucose_decay_per_hour * c.glucose_sleep_decay_factor,
    SleepState.REM: lambda c: -c.glucose_decay_per_hour * c.glucose_sleep_decay_factor,
}

# 血糖自身也分档调：高了压得快，低了肝糖原顶上（否则会一路掉到 0）
_GLUCOSE_BAND = {
    GlycemiaState.HIGH: lambda c: c.glucose_decay_high_factor,
    GlycemiaState.NORMAL: lambda c: 1.0,
    GlycemiaState.LOW: lambda c: c.glucose_decay_low_factor,
}

# 胃排空速度：血糖高时饱得久，血糖低时掉得快
_FULLNESS_RATE = {
    GlycemiaState.HIGH: lambda c: -c.fullness_decay_per_hour * c.fullness_decay_high_factor,
    GlycemiaState.NORMAL: lambda c: -c.fullness_decay_per_hour,
    GlycemiaState.LOW: lambda c: -c.fullness_decay_per_hour * c.fullness_decay_low_factor,
}


class EnergyDynamics(Effect):
    """能量：按睡眠相位回/耗，血糖过低时额外掉。"""
    name = "energy"
    control = ControlKind.AUTONOMIC

    def __init__(self, cfg: BioSimConfig, params=None) -> None:
        super().__init__(cfg, params)
        self._inf = Influence()

    def _compute_influence(self, state, cfg, tick):
        glycemia = glycemia_of(state.current_state.glucose, cfg)
        self._inf.delta.energy = _ENERGY_RATE[state.sleep](cfg) + _ENERGY_GLYCEMIA[glycemia](cfg)
        return self._inf


class GlucoseDynamics(Effect):
    """血糖：持续被消耗，睡眠时消耗慢；补上来靠消化（进食效果）。"""
    name = "glucose"
    control = ControlKind.AUTONOMIC

    def __init__(self, cfg: BioSimConfig, params=None) -> None:
        super().__init__(cfg, params)
        self._inf = Influence()

    def _compute_influence(self, state, cfg, tick):
        glycemia = glycemia_of(state.current_state.glucose, cfg)
        self._inf.delta.glucose = _GLUCOSE_RATE[state.sleep](cfg) * _GLUCOSE_BAND[glycemia](cfg)
        return self._inf


class FullnessDynamics(Effect):
    """饱腹：胃里装了多少。下降速度绑在血糖浓度上，不是固定斜率。"""
    name = "fullness"
    control = ControlKind.AUTONOMIC

    def __init__(self, cfg: BioSimConfig, params=None) -> None:
        super().__init__(cfg, params)
        self._inf = Influence()

    def _compute_influence(self, state, cfg, tick):
        glycemia = glycemia_of(state.current_state.glucose, cfg)
        self._inf.delta.fullness = _FULLNESS_RATE[glycemia](cfg)
        return self._inf


class StressDynamics(Effect):
    name = "stress"
    control = ControlKind.AUTONOMIC

    def __init__(self, cfg: BioSimConfig, params=None) -> None:
        super().__init__(cfg, params)
        self._inf = Influence()

    def _compute_influence(self, state, cfg, tick):
        energy = state.current_state.energy
        hunger = hunger_of(state.current_state.fullness, cfg)
        deficit = max(0.0, (cfg.stress_energy_low_threshold - energy) / cfg.stress_energy_low_threshold)
        hunger_gain = {HungerState.HUNGRY: cfg.stress_hunger_factor}.get(hunger, 0.0)
        doze_gain = {SleepState.DOZING: cfg.stress_doze_factor}.get(state.sleep, 0.0)
        gain = cfg.stress_rise_rate * (deficit + hunger_gain + doze_gain)

        if energy > cfg.stress_energy_low_threshold and hunger is not HungerState.HUNGRY:
            loss = cfg.stress_fall_rate
        elif energy > cfg.stress_energy_low_threshold * 0.5 and hunger is HungerState.CONTENT:
            loss = cfg.stress_fall_rate * 0.5
        else:
            loss = 0.0

        stress = state.stress + (gain - loss) * tick.dt
        self._inf.aspects["stress"] = min(100.0, max(0.0, stress))
        return self._inf


class MoodDynamics(Effect):
    name = "mood"
    control = ControlKind.AUTONOMIC

    def __init__(self, cfg: BioSimConfig, params=None) -> None:
        super().__init__(cfg, params)
        self._inf = Influence()

    def _compute_influence(self, state, cfg, tick):
        energy = state.current_state.energy
        target = 100.0 - state.stress
        if state.stress <= cfg.stress_happy_threshold and energy >= cfg.stress_happy_energy_required:
            target = max(target, 85.0)
        elif state.stress >= cfg.stress_irritable_threshold:
            target = min(target, 20.0)
        target = min(cfg.mood_max, max(cfg.mood_min, target))
        self._inf.delta.mood = (target - state.current_state.mood) * cfg.mood_response_rate
        return self._inf


# --- 连续值 -> 离散枚举 的语义映射 ---

def glycemia_of(glucose: float, cfg) -> GlycemiaState:
    if glucose >= cfg.glucose_high_threshold:
        return GlycemiaState.HIGH
    if glucose <= cfg.glucose_low_threshold:
        return GlycemiaState.LOW
    return GlycemiaState.NORMAL


def hunger_of(fullness: float, cfg) -> HungerState:
    if fullness >= cfg.fullness_full_threshold:
        return HungerState.FULL
    if fullness >= cfg.fullness_content_threshold:
        return HungerState.CONTENT
    return HungerState.HUNGRY


def mood_of(mood: float, cfg) -> MoodState:
    if mood >= cfg.mood_happy_threshold:
        return MoodState.HAPPY
    if mood <= cfg.mood_irritable_threshold:
        return MoodState.IRRITABLE
    return MoodState.NEUTRAL

