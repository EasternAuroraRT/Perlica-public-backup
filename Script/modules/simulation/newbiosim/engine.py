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
from .physiology import EnergyDynamics, FullnessDynamics, StressDynamics, MoodDynamics, hunger_of, mood_of


class BioSimEngine:
    """中心化执行者：汇总影响、门控、积分、钳制、回收。"""

    _BASE = (EnergyDynamics, FullnessDynamics, StressDynamics, MoodDynamics)

    def __init__(self, config=None, start_hour=8.0, update_interval=0.1, time_scale=1.0):
        self.cfg = config if config is not None else default_config()
        self.update_interval = update_interval
        self.time_scale = time_scale

        energy = self.cfg.energy_max * 0.8
        self._bio = BioState(
            initial_state=StateVec(energy=energy, fullness=self.cfg.fullness_max, mood=50.0),
            sleep=SleepState.AWAKE,
            activity=ActivityLevel.MODERATE,
            stress=0.0,
            clock_hour=start_hour % 24.0,
            elapsed_hours=0.0,
            sleep_duration=0.0,
            doze_timer=0.0,
            exercise_timer=0.0,
        )

        self._tick = Tick()
        self._net = StateVec()
        self._effects: list[Effect] = [cls(self.cfg) for cls in self._BASE]

        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._base_sim_hours = 0.0
        self._segment_start = time.monotonic()
        self._last_sim_hours = 0.0

    # ---------- 状态查询 ----------
    @property
    def bio(self) -> BioState:
        with self._lock:
            return self._bio

    @property
    def current(self) -> StateVec:
        with self._lock:
            s = self._bio.current_state
            return StateVec(energy=s.energy, fullness=s.fullness, mood=s.mood)

    def snapshot(self) -> dict:
        with self._lock:
            st = self._bio.current_state
            return {
                "time": self._bio.clock_hour,
                "elapsed_hours": self._bio.elapsed_hours,
                "sleep": self._bio.sleep,
                "activity": self._bio.activity,
                "hunger": hunger_of(st.fullness, self.cfg),
                "mood": mood_of(st.mood, self.cfg),
                "energy": st.energy,
                "fullness": st.fullness,
                "mood_score": st.mood,
                "stress": self._bio.stress,
                "sleep_duration": self._bio.sleep_duration,
            }

    # ---------- 动作 ----------
    def available_actions(self) -> list[str]:
        with self._lock:
            return [cls.name for _t, cls in _EFFECTS.items()
                    if cls.when is None or cls.when(self._bio, self.cfg)]

    def act(self, params) -> None:
        with self._lock:
            cls = _EFFECTS.get(type(params))
            if cls is None:
                raise ValueError(f"no effect for {type(params).__name__}")
            if not (cls.when is None or cls.when(self._bio, self.cfg)):
                raise ValueError(f"action {cls.name!r} not allowed now")
            eff = cls(self.cfg, params)
            inf = eff.influence(self._bio, self.cfg, self._tick)
            for k, v in inf.aspects.items():
                self._bio.set_aspect(k, v)
            if eff.persistent:
                self._effects.append(eff)
            else:
                eff.on_expire(self._bio, self.cfg, self._tick)

    def cancel(self, name: str) -> None:
        """自主动手：只允许 volitional 的常驻效果。"""
        with self._lock:
            for eff in self._effects:
                if eff.name == name and eff.control is ControlKind.VOLITIONAL:
                    eff.on_expire(self._bio, self.cfg, self._tick)
                    self._effects.remove(eff)
                    return
            raise ValueError(f"no volitional effect named {name!r}")

    def interrupt(self, name: str, by: WakeSource = WakeSource.EXTERNAL) -> None:
        """外界打断：只允许 interruptible 的效果（如睡眠）。"""
        with self._lock:
            for eff in self._effects:
                if eff.name == name and eff.interruptible:
                    eff.on_expire(self._bio, self.cfg, self._tick)
                    self._effects.remove(eff)
                    return
            raise ValueError(f"no interruptible effect named {name!r}")

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
        s.energy = min(self.cfg.energy_max, max(self.cfg.energy_min, s.energy))
        s.fullness = min(self.cfg.fullness_max, max(self.cfg.fullness_min, s.fullness))
        s.mood = min(self.cfg.mood_max, max(self.cfg.mood_min, s.mood))

