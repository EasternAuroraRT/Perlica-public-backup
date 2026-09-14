"""时钟：按真实时间驱动计算器。它不认识业务，也不改状态。"""
from __future__ import annotations

import threading
import time

from .BioEngine import BioEngine


class EngineClock:
    """把真实时间换算成模拟小时，按间隔推引擎。

    时间倍率属于时钟，不属于计算器 —— 计算器只认 dt。
    """

    def __init__(self, engine: BioEngine, update_interval: float = 0.1,
                 time_scale: float = 1.0) -> None:
        self.engine = engine
        self.update_interval = update_interval
        self.time_scale = time_scale
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._base_sim_hours = 0.0
        self._segment_start = time.monotonic()
        self._last_sim_hours = 0.0

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
                self.engine.advance(delta)
                self._last_sim_hours = target

    def set_time_scale(self, scale: float) -> None:
        with self._lock:
            self._base_sim_hours = self._sim_hours_now()
            self._segment_start = time.monotonic()
            self.time_scale = max(0.0, scale)

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
                    self.engine.advance(delta)
                    self._last_sim_hours = target
