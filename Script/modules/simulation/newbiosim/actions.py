"""动作层：类型化命令参数 + 对应效果插件（经 @effect 注册）。"""
from __future__ import annotations

from dataclasses import dataclass

from Script.modules.simulation.newbiosim.config import BioSimConfig

from .types import BioState, StateVec, Influence
from .types import SleepState, ActivityLevel, HungerState, MoodState
from .enums import ControlKind
from .core import Effect, effect


# --- 类型化命令参数（构造即静态合法） ---

@dataclass(frozen=True)
class EatParams:
    portion: float = 0.5
    quality: float = 1.0


@dataclass(frozen=True)
class ExerciseParams:
    intensity: float = 1.0
    minutes: float = 20.0


@dataclass(frozen=True)
class SleepParams:
    pass


@dataclass(frozen=True)
class WakeParams:
    pass


# --- 动作效果 ---

@effect(EatParams)
class DigestEffect(Effect):
    """进食：消化期持续抬升饱腹/能量/心情；autonomic，不可取消/不可外部打断。"""
    name = "eat"
    control = ControlKind.AUTONOMIC
    when = classmethod(lambda cls, s, c: True)

    def __init__(self, cfg, params):
        super().__init__(cfg, params)
        p = params
        self.remaining = cfg.digest_duration * p.portion
        self._fullness_rate = cfg.digest_fullness_gain * p.portion / max(self.remaining, 1e-9)
        self._energy_rate = cfg.digest_energy_gain * p.quality / max(self.remaining, 1e-9)
        self._mood_rate = cfg.digest_mood_gain * min(p.portion, 0.8) / max(self.remaining, 1e-9)
        self._inf = Influence()

    def _compute_influence(self, state, cfg, tick):
        self.remaining -= tick.dt
        self._inf.delta.fullness = self._fullness_rate
        self._inf.delta.energy = self._energy_rate
        self._inf.delta.mood = self._mood_rate
        return self._inf

    def alive(self, state, cfg, tick):
        return self.remaining > 0


@effect(ExerciseParams)
class ExerciseEffect(Effect):
    """运动：持续能耗 + 切活跃相位；volitional，可自主动手取消。"""
    name = "exercise"
    control = ControlKind.VOLITIONAL
    when = classmethod(lambda cls, s, c: s.sleep is SleepState.AWAKE)

    def __init__(self, cfg, params):
        super().__init__(cfg, params)
        self.remaining = params.minutes / 60.0
        self._energy_rate = cfg.exercise_energy_rate * params.intensity
        self._inf = Influence()

    def _compute_influence(self, state, cfg, tick):
        self.remaining -= tick.dt
        self._inf.delta.energy = -self._energy_rate
        self._inf.aspects["activity"] = ActivityLevel.ACTIVE
        self._inf.aspects["exercise_timer"] = cfg.exercise_cooldown
        return self._inf

    def alive(self, state, cfg, tick):
        return self.remaining > 0


@effect(SleepParams)
class SleepEffect(Effect):
    """入睡：autonomic + 可被外界打断；驱动睡眠相位循环。"""
    name = "sleep"
    control = ControlKind.AUTONOMIC
    interruptible = True
    when = classmethod(lambda cls, s, c: s.sleep in (SleepState.AWAKE, SleepState.DOZING))

    def __init__(self, cfg: BioSimConfig, params=None) -> None:
        super().__init__(cfg, params)
        self._inf = Influence()

    def _compute_influence(self, state, cfg, tick):
        if state.sleep is SleepState.AWAKE:
            self._inf.aspects["sleep"] = SleepState.LIGHT
            self._inf.aspects["sleep_duration"] = 0.0
            self._inf.aspects["doze_timer"] = 0.0
            self._inf.aspects["exercise_timer"] = 0.0
            return self._inf
        if state.sleep in (SleepState.LIGHT, SleepState.DEEP, SleepState.REM):
            sd = state.sleep_duration + tick.dt
            self._inf.aspects["sleep_duration"] = sd
            if sd >= cfg.sleep_full_duration:
                self._inf.aspects["sleep"] = SleepState.DOZING
                self._inf.aspects["doze_timer"] = 0.0
            else:
                pos = (sd % 1.5) / 1.5
                next_phase = SleepState.LIGHT if pos < 0.2 else SleepState.DEEP if pos < 0.6 else SleepState.REM
                self._inf.aspects["sleep"] = next_phase
        elif state.sleep is SleepState.DOZING:
            dtm = state.doze_timer + tick.dt
            self._inf.aspects["doze_timer"] = dtm
            self._inf.aspects["sleep_duration"] = state.sleep_duration + tick.dt
            if dtm >= cfg.doze_timeout:
                self._inf.aspects["sleep"] = SleepState.AWAKE
                self._inf.aspects["sleep_duration"] = 0.0
                self._inf.aspects["doze_timer"] = 0.0
        return self._inf

    def alive(self, state, cfg, tick):
        return state.sleep is not SleepState.AWAKE

    def on_expire(self, state, cfg, tick):
        state.set_aspect("sleep", SleepState.AWAKE)
        state.set_aspect("sleep_duration", 0.0)
        state.set_aspect("doze_timer", 0.0)


@effect(WakeParams)
class WakeEffect(Effect):
    """唤醒：一次性（非 persistent），立即回到清醒。"""
    name = "wake"
    control = ControlKind.VOLITIONAL
    persistent = False
    when = classmethod(lambda cls, s, c: s.sleep is not SleepState.AWAKE)

    def __init__(self, cfg: BioSimConfig, params=None) -> None:
        super().__init__(cfg, params)
        self._inf = Influence()

    def _compute_influence(self, state, cfg, tick):
        self._inf.aspects["sleep"] = SleepState.AWAKE
        self._inf.aspects["sleep_duration"] = 0.0
        self._inf.aspects["doze_timer"] = 0.0
        return self._inf

