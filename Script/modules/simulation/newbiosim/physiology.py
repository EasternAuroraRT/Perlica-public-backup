"""常驻生理效果：每个连续维度自述变化率，查表而非 if 链。"""
from __future__ import annotations

from .types import BioState, StateVec, Influence
from .types import SleepState, ActivityLevel, HungerState, MoodState
from .enums import ControlKind
from .core import Effect, Influence
from .config import BioSimConfig

_ENERGY_RATE = {
    SleepState.AWAKE: lambda c: -c.energy_awake_cost,
    SleepState.DOZING: lambda c: c.energy_doze_recover,
    SleepState.LIGHT: lambda c: c.energy_sleep_recover,
    SleepState.DEEP: lambda c: c.energy_sleep_recover,
    SleepState.REM: lambda c: c.energy_sleep_recover,
}


class EnergyDynamics(Effect):
    name = "energy"
    control = ControlKind.AUTONOMIC

    def __init__(self, cfg: BioSimConfig, params=None) -> None:
        super().__init__(cfg, params)
        self._inf = Influence()

    def _compute_influence(self, state, cfg, tick):
        self._inf.delta.energy = _ENERGY_RATE[state.sleep](cfg)
        return self._inf


class FullnessDynamics(Effect):
    name = "fullness"
    control = ControlKind.AUTONOMIC

    def __init__(self, cfg: BioSimConfig, params=None) -> None:
        super().__init__(cfg, params)
        self._inf = Influence()

    def _compute_influence(self, state, cfg, tick):
        self._inf.delta.fullness = -cfg.fullness_decay_per_hour
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

