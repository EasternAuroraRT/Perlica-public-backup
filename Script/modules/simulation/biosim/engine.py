"""执行层：中心化执行者，唯一有权修改状态的地方。"""
from __future__ import annotations

import time
import threading

from .types import BioState, StateVec, SleepState, ActivityLevel
from .config import default_config
from .types.Tick import Tick
from .core import Effect, _EFFECTS
from .enums import ControlKind, WakeSource
from . import actions  # noqa: F401  触发动作注册
from .physiology import (EnergyDynamics, GlucoseDynamics, FullnessDynamics, StressDynamics, MoodDynamics,
                         hunger_of, mood_of, glycemia_of)
from .observation import Observation


class BioSimEngine:
    """中心化执行者：汇总影响、门控、积分、钳制、回收。

    常驻效果在构造时按 BASE_EFFECTS 实例化一次；连续维度的初始值与上下限按
    "<名>_initial / _min / _max" 的约定从配置里取 —— 加一个维度只要动
    StateVec.elements 和 config.py，引擎本身不用改。

    base_effects 可以整组换掉常驻模拟项（黑箱化的入口：换模拟不换接口）。
    """

    BASE_EFFECTS = (EnergyDynamics, GlucoseDynamics, FullnessDynamics, StressDynamics, MoodDynamics)

    def __init__(self, config=None, start_hour=8.0, update_interval=0.1, time_scale=1.0,
                 base_effects: tuple[type[Effect], ...] | None = None):
        self.cfg = config if config is not None else default_config()
        self.update_interval = update_interval
        self.time_scale = time_scale

        self._bounds = tuple(
            (d.name, getattr(self.cfg, d.name + "_min"), getattr(self.cfg, d.name + "_max"))
            for d in StateVec.elements
        )
        initial = StateVec(**{d.name: getattr(self.cfg, d.name + "_initial")
                              for d in StateVec.elements})

        self._bio = BioState(
            initial_state=initial,
            sleep=SleepState.AWAKE,
            activity=ActivityLevel.MODERATE,
            stress=0.0,
            clock_hour=start_hour % 24.0,
            elapsed_hours=0.0,
            sleep_duration=0.0,
            last_sleep_duration=0.0,
            doze_timer=0.0,
            exercise_timer=0.0,
        )

        self._tick = Tick()
        self._net = StateVec()
        resident = self.BASE_EFFECTS if base_effects is None else base_effects
        self._effects: list[Effect] = [cls(self.cfg) for cls in resident]

        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._base_sim_hours = 0.0
        self._segment_start = time.monotonic()
        self._last_sim_hours = 0.0

    # ---------- 状态查询 ----------
    def observe(self) -> Observation:
        """类型化读模型：核心只交数据，怎么措辞是消费方的事。"""
        with self._lock:
            st = self._bio.current_state
            return Observation(
                sleep=self._bio.sleep,
                activity=self._bio.activity,
                hunger=hunger_of(st.fullness, self.cfg),
                mood=mood_of(st.mood, self.cfg),
                glycemia=glycemia_of(st.glucose, self.cfg),
                energy=st.energy,
                fullness=st.fullness,
                mood_score=st.mood,
                glucose=st.glucose,
                stress=self._bio.stress,
                time=self._bio.clock_hour,
                elapsed_hours=self._bio.elapsed_hours,
                sleep_duration=self._bio.sleep_duration,
                last_sleep_duration=self._bio.last_sleep_duration,
            )

    # ---------- 动作 ----------
    def available_actions(self) -> list[str]:
        with self._lock:
            return [cls.name for _t, cls in _EFFECTS.items()
                    if cls.refusal(self._bio, self.cfg) is None]

    def act_by_name(self, action: str, **params) -> None:
        """按动作名下达命令（使用方的语言：名字 + 参数）。"""
        for params_type, effect_type in _EFFECTS.items():
            if effect_type.name == action:
                self.act(params_type(**params))
                return
        raise ValueError(f"unknown action: {action!r}")

    def act(self, params) -> None:
        with self._lock:
            cls = _EFFECTS.get(type(params))
            if cls is None:
                raise ValueError(f"no effect for {type(params).__name__}")
            reason = cls.refusal(self._bio, self.cfg)
            if reason is not None:
                raise ValueError(reason)
            # 下达那一刻 dt=0：效果在这里只做落地动作，不消耗任何时间
            self._tick.set(0.0, self._bio.clock_hour, self._bio.elapsed_hours, self.time_scale)
            eff = cls(self.cfg, params)
            inf = eff.influence(self._bio, self.cfg, self._tick)
            for k, v in inf.aspects.items():
                self._bio.set_aspect(k, v)
            self._bio.current_state += inf.instant
            inf.instant.zero()
            self._clamp_state()
            if eff.persistent:
                self._effects.append(eff)
            else:
                eff.on_expire(self._bio, self.cfg, self._tick)

    def cancel(self, name: str) -> None:
        """自主动手：停掉某个 volitional 效果；本来没在跑就什么都不做。"""
        self._detach(name, ControlKind.VOLITIONAL)

    def interrupt(self, name: str, by: WakeSource = WakeSource.EXTERNAL) -> None:
        """外界打断：停掉某个 interruptible 效果；本来没在跑就什么都不做。"""
        self._detach(name, None, interruptible=True)

    def _detach(self, name: str, control, interruptible: bool = False) -> None:
        if not any(cls.name == name for cls in _EFFECTS.values()):
            raise ValueError(f"unknown effect: {name!r}")
        with self._lock:
            for eff in self._effects:
                if eff.name != name:
                    continue
                allowed = eff.interruptible if interruptible else eff.control is control
                if allowed:
                    eff.on_expire(self._bio, self.cfg, self._tick)
                    self._effects.remove(eff)
                    return

    # ---------- 时间推进 ----------
    def advance(self, hours: float) -> None:
        step = self.cfg.time_step
        remaining = hours
        while remaining > 0:
            delta = min(step, remaining)
            with self._lock:
                self._update(delta)
            remaining -= delta

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        with self._lock:
            self._base_sim_hours = 0.0
            self._segment_start = time.monotonic()
            self._last_sim_hours = 0.0
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        self._thread = None
        with self._lock:
            target = self._sim_hours_now()
            delta = target - self._last_sim_hours
            if delta > 0:
                self.advance(delta)
                self._last_sim_hours = target

    def set_time_scale(self, scale: float) -> None:
        with self._lock:
            scale = max(0.0, scale)
            self._base_sim_hours = self._sim_hours_now()
            self._segment_start = time.monotonic()
            self.time_scale = scale

    # ---------- 内部 ----------
    def _sim_hours_now(self) -> float:
        return self._base_sim_hours + (time.monotonic() - self._segment_start) * self.time_scale / 3600.0

    def _run(self) -> None:
        while not self._stop_event.is_set():
            time.sleep(self.update_interval)
            if self._stop_event.is_set():
                break
            with self._lock:
                target = self._sim_hours_now()
                delta = target - self._last_sim_hours
                if delta > 0:
                    self.advance(delta)
                    self._last_sim_hours = target

    def _update(self, dt: float) -> None:
        tick = self._tick
        tick.set(dt, self._bio.clock_hour, self._bio.elapsed_hours, self.time_scale)

        net = self._net
        net.zero()
        for eff in self._effects:
            inf = eff.influence(self._bio, self.cfg, tick)
            net += inf.delta
            for k, v in inf.aspects.items():
                self._bio.set_aspect(k, v)

        self._bio.current_state.add_scaled(net, tick.dt)
        self._clamp_state()

        self._bio.set_aspect("clock_hour", (self._bio.clock_hour + dt) % 24.0)
        self._bio.set_aspect("elapsed_hours", self._bio.elapsed_hours + dt)

        kept = []
        for eff in self._effects:
            if eff.alive(self._bio, self.cfg, tick):
                kept.append(eff)
            else:
                eff.on_expire(self._bio, self.cfg, tick)
        self._effects = kept

    def _clamp_state(self) -> None:
        s = self._bio.current_state
        for name, low, high in self._bounds:
            value = getattr(s, name)
            if value < low:
                setattr(s, name, low)
            elif value > high:
                setattr(s, name, high)

