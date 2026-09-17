"""引擎：计算器 + 时间所有者。

- 只做积分：效果声明 → 引擎整合 → 唯一改状态的地方。
- 时间也归它：持有 `integration_step_hours`（精度）与 `sim_hours_per_real_hour`（缩放），
  在读取 / 改状态 / 存档前把模拟同步到"现在"。真实时间是唯一真相，没有独立的时钟对象。
- 不认识业务、不认识动作、不知道措辞。
"""
from __future__ import annotations

import io
import math
import os
import pickle
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, TypedDict, cast

from .Effect import Effect
from .EngineSlice import EngineSlice
from .BasicEffects.physiology import glycemia_of, hunger_of, mood_of
from .types import ActivityLevel, Aspects, BioState, SleepState, StateVec, Tick


class CheckpointPayload(TypedDict):
    """存档内容。版本头单独放在文件开头（见 `_checkpoint_header`），不在这里。"""
    format: int
    state: BioState
    effects: list[Effect]
    saved_at_unix_seconds: float
    sim_hours_per_real_hour: float


class _SafeUnpickler(pickle.Unpickler):
    """只认白名单里的类。

    存档是磁盘上的不可信输入：坏档、被人塞私货，都可能在 `pickle.load` 阶段执行任意代码。
    这里只放行 builtins / random / biosim 自己的类；别的直接判为坏档。
    """

    _ALLOWED_MODULES = ("builtins", "random")

    def find_class(self, module: str, name: str) -> Any:
        if module in self._ALLOWED_MODULES or module.startswith("modules.simulation.biosim"):
            return super().find_class(module, name)
        raise pickle.UnpicklingError(f"存档里出现不允许的类: {module}.{name}")


def _is_real_number(value: object) -> bool:
    """真·数字（排除 bool——它是 int 的子类，别让它混进来）。"""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


class BioEngine:
    """计算器 + 时间所有者：挂效果、按真实时间推进、交读数。

    概念上只有两个模拟参数：
        integration_step_hours    积分精度（把区间切多细）
        sim_hours_per_real_hour   模拟缩放（模拟小时 / 真实小时）
    推进一步时，`_step` 收到的 dt 就是实际推进的模拟时长（整步 = 精度，末步 = 零头），
    效果器据此计算；没有人需要关心"多久调一次"。
    """

    def __init__(self, *, start_clock_hour: float = 8.0, integration_step_hours: float = 0.05,
                 sim_hours_per_real_hour: float = 1.0,
                 checkpoint: str | Path | None = None) -> None:
        if integration_step_hours <= 0:
            raise ValueError("integration_step_hours 必须为正数")
        self.integration_step_hours = integration_step_hours
        self.sim_hours_per_real_hour = max(0.0, sim_hours_per_real_hour)
        self._dimensions = {d.name: d for d in StateVec.elements}

        initial = StateVec(**{d.name: d.initial for d in StateVec.elements})
        self._bio = BioState(
            initial_state=initial,
            aspects=Aspects(
                sleep=SleepState.AWAKE,
                activity=ActivityLevel.MODERATE,
                stress=0.0,
                clock_hour=start_clock_hour % 24.0,
                elapsed_hours=0.0,
                sleep_duration_hours=0.0,
                last_sleep_duration_hours=0.0,
                digest_left_hours=0.0,
                exercise_left_hours=0.0,
            ),
        )

        self._effects: list[Effect] = []
        self._tick = Tick()
        self._net = StateVec()
        self._mul = StateVec()
        self._mul.fill(1.0)
        self._lock = threading.RLock()
        self._last_sync_unix_seconds = time.time()

        self.loaded_from_checkpoint = False
        self.load_error: str | None = None   # 读档失败原因（启动不该因此崩）

        self._checkpoint_dir = Path(checkpoint) if checkpoint is not None else None
        if self._checkpoint_dir is not None:
            try:
                self.loaded_from_checkpoint = self.load_checkpoint(self._checkpoint_dir)
            except Exception as e:
                # 旧版本 / 损坏的档：明确记录，然后当新的一天。退出时会把新档写回去，自愈。
                self.load_error = f"{type(e).__name__}: {e}"
        self._last_sync_unix_seconds = time.time()

    # ---------- 时间 ----------
    def sync(self) -> None:
        """把模拟同步到此刻（读取 / 改状态 / 存档前都会自动做）。"""
        with self._lock:
            self._sync_locked()

    def _sync_locked(self) -> None:
        """按挂钟把模拟推到此刻。调用方需持有锁。"""
        now = time.time()
        due_hours = (now - self._last_sync_unix_seconds) * self.sim_hours_per_real_hour / 3600.0
        self._last_sync_unix_seconds = now
        if due_hours > 0.0:
            self.advance(due_hours)      # RLock 可重入

    def set_sim_hours_per_real_hour(self, sim_hours_per_real_hour: float) -> None:
        with self._lock:
            self._sync_locked()          # 先按旧倍率结算，再改
            self.sim_hours_per_real_hour = max(0.0, sim_hours_per_real_hour)

    # ---------- 存档 ----------
    CHECKPOINT_FILE = "checkpoint.pkl"
    # 改过字段名就把它 +1：旧档会被明确判为"版本不认识"，而不是死在 pickle 反序列化里
    CHECKPOINT_FORMAT = 2
    CHECKPOINT_MAGIC = b"biosim-checkpoint"

    def _checkpoint_header(self) -> bytes:
        """文件开头的版本头：先读它就能判版本，不必先反序列化整个 payload。"""
        return self.CHECKPOINT_MAGIC + b" v" + str(self.CHECKPOINT_FORMAT).encode() + b"\n"

    def save_checkpoint(self, dirpath: str | Path | None = None) -> Path:
        """同步到此刻 → 在锁内取切片 → 锁外落盘（IO 不占锁）。"""
        directory = Path(dirpath) if dirpath is not None else self._checkpoint_dir
        if directory is None:
            raise ValueError("没有存档路径：要么传参数，要么构造时给 checkpoint")
        with self._lock:
            self._sync_locked()
            payload: CheckpointPayload = {
                "format": self.CHECKPOINT_FORMAT,
                "state": self._bio,
                "effects": list(self._effects),
                "saved_at_unix_seconds": self._last_sync_unix_seconds,
                "sim_hours_per_real_hour": self.sim_hours_per_real_hour,
            }
            blob = self._checkpoint_header() + pickle.dumps(payload, protocol=pickle.HIGHEST_PROTOCOL)

        # 原子写：先写 .tmp 再替换 —— 读半个存档比读不到更糟
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / self.CHECKPOINT_FILE
        temp = target.with_name(target.name + ".tmp")
        with open(temp, "wb") as handle:
            handle.write(blob)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, target)
        return target

    def load_checkpoint(self, dirpath: str | Path | None = None) -> bool:
        """从目录恢复。**那里没有存档就当新的一天**（返回 False），不是错误。

        文件在、却读不了则照抛（UnpicklingError / ValueError）—— 那是异常，
        不该被当成"还没有存档"悄悄吞掉。

        跨天（存档日期 != 今天）同样不恢复、当新的一天（也返回 False）——
        外界事件不该被"补"出来。同日则把停机那段按倍率补算进状态。
        """
        directory = Path(dirpath) if dirpath is not None else self._checkpoint_dir
        if directory is None:
            raise ValueError("没有存档路径：要么传参数，要么构造时给 checkpoint")
        target = directory / self.CHECKPOINT_FILE
        if not target.exists():
            return False
        blob = target.read_bytes()
        newline = blob.find(b"\n")
        header = blob[:newline] if newline >= 0 else blob
        if header != self._checkpoint_header().rstrip(b"\n"):
            raise ValueError(
                f"存档版本不认识（头部 {header[:32]!r}）: {target}；"
                f"当前需要 v{self.CHECKPOINT_FORMAT}"
            )
        payload = self._validate_payload(_SafeUnpickler(io.BytesIO(blob[newline + 1:])).load())

        # 兼容旧键名：saved_at / time_scale
        gap_hours = 0.0
        saved_at_unix_seconds = payload.get("saved_at_unix_seconds", payload.get("saved_at"))
        if saved_at_unix_seconds is not None:
            saved_dt = datetime.fromtimestamp(float(saved_at_unix_seconds))
            now_dt = datetime.now()
            if saved_dt.date() != now_dt.date():
                return False
            self.sim_hours_per_real_hour = float(
                payload.get("sim_hours_per_real_hour", payload.get("time_scale", 1.0))
            )
            gap_hours = max(
                0.0,
                (now_dt - saved_dt).total_seconds() / 3600.0 * self.sim_hours_per_real_hour,
            )

        with self._lock:
            self._bio = payload["state"]
            self._effects = list(payload["effects"])
            self._clamp_state()     # 数值即使合法也可能越界，统一夹回范围

        if gap_hours > 0:
            self.advance(gap_hours)
        return True

    def _validate_payload(self, payload: object) -> CheckpointPayload:
        """把不可信的存档内容校验成 CheckpointPayload；不合法就抛 ValueError。"""
        if not isinstance(payload, dict):
            raise ValueError("存档内容不是对象")
        if payload.get("format") != self.CHECKPOINT_FORMAT:
            raise ValueError("存档内容与版本不匹配")
        state = payload.get("state")
        effects = payload.get("effects")
        saved_at = payload.get("saved_at_unix_seconds")
        scale = payload.get("sim_hours_per_real_hour", 1)
        if not isinstance(state, BioState):
            raise ValueError("存档 state 类型不对")
        if not isinstance(effects, list) or not all(isinstance(e, Effect) for e in effects):
            raise ValueError("存档 effects 里混了非效果对象")
        if not _is_real_number(saved_at):
            raise ValueError("存档 saved_at_unix_seconds 不是数字")
        if not _is_real_number(scale) or scale < 0.0:
            raise ValueError("存档 sim_hours_per_real_hour 不合法")
        aspects = state.aspects
        if not isinstance(aspects.sleep, SleepState) or not isinstance(aspects.activity, ActivityLevel):
            raise ValueError("存档 aspects 枚举不合法")
        for name in ("stress", "clock_hour", "elapsed_hours", "sleep_duration_hours",
                     "last_sleep_duration_hours", "digest_left_hours", "exercise_left_hours"):
            value = getattr(aspects, name)
            if not _is_real_number(value) or not math.isfinite(value):
                raise ValueError(f"存档 aspects.{name} 不合法: {value!r}")
        for dim in StateVec.elements:
            value = getattr(state.current_state, dim.name)
            if not _is_real_number(value) or not math.isfinite(value):
                raise ValueError(f"存档维度 {dim.name} 不合法: {value!r}")
        return cast(CheckpointPayload, payload)

    # ---------- 挂载 ----------
    def add_effect(self, effect: Effect) -> bool:
        with self._lock:
            self._sync_locked()
            attached = self._attach(effect)
            self._reap(self._tick)
            return attached

    def add_effects(self, *effects: Effect) -> int:
        with self._lock:
            self._sync_locked()
            attached = sum(1 for effect in effects if self._attach(effect))
            self._reap(self._tick)
            return attached

    def _attach(self, effect: Effect) -> bool:
        observation = self._slice_locked()
        if effect.refusal(observation) is not None:
            return False
        self._tick.set(0.0, observation.clock_hour, observation.elapsed_hours)
        self._apply(effect.influence(self._bio, self._tick))
        self._effects.append(effect)
        return True

    def remove_effect(self, effect: Effect) -> bool:
        """摘掉一个效果。不在列表里就什么都不做。"""
        with self._lock:
            self._sync_locked()
            if effect not in self._effects:
                return False
            self._effects.remove(effect)
            return True

    @property
    def effects(self) -> tuple[Effect, ...]:
        """当前挂着的效果（只读快照）。"""
        with self._lock:
            return tuple(self._effects)

    @property
    def elapsed_hours(self) -> float:
        """累计模拟小时（含同步到此刻的部分）。"""
        self.sync()
        with self._lock:
            return self._bio.aspects.elapsed_hours

    # ---------- 读数 ----------
    def get_slice(self) -> EngineSlice:
        """类型化读模型：先同步到此刻，再交数据。"""
        with self._lock:
            self._sync_locked()
            return self._slice_locked()

    def _slice_locked(self) -> EngineSlice:
        st = self._bio.current_state
        a = self._bio.aspects
        return EngineSlice(
            sleep=a.sleep,
            activity=a.activity,
            hunger=hunger_of(st.fullness),
            mood=mood_of(st.mood),
            glycemia=glycemia_of(st.glucose),
            energy=st.energy,
            fullness=st.fullness,
            mood_score=st.mood,
            glucose=st.glucose,
            stress=a.stress,
            clock_hour=a.clock_hour,
            elapsed_hours=a.elapsed_hours,
            sleep_duration_hours=a.sleep_duration_hours,
            last_sleep_duration_hours=a.last_sleep_duration_hours,
        )

    # ---------- 计算 ----------
    def advance(self, sim_hours: float) -> None:
        """把模拟时间推进 sim_hours：按 integration_step_hours 走整步，零头结算。

        `_step` 拿到的 dt 就是**实际推进的模拟时长**；效果器据此计算。
        """
        step = self.integration_step_hours
        remaining_hours = sim_hours
        with self._lock:
            while remaining_hours > 1e-12:
                dt_hours = step if remaining_hours >= step else remaining_hours
                self._step(dt_hours)
                remaining_hours -= dt_hours

    # ---------- 内部 ----------
    def _step(self, dt_hours: float) -> None:
        tick = self._tick
        a = self._bio.aspects
        tick.set(dt_hours, a.clock_hour, a.elapsed_hours)

        net = self._net
        mul = self._mul
        net.zero()
        mul.fill(1.0)                       # 乘区单位元：没人声明就是 1.0
        for eff in self._effects:
            inf = eff.influence(self._bio, tick)
            net += inf.delta_per_hour           # 基础值（每小时变化率）：求和
            mul.multiply_by_shifted(inf.mul)    # 乘区：并进 (1 + 提交倍率)
            inf.aspects.apply_to(a)             # 方面：只覆盖声明里给了的字段

        net *= mul                          # 实际修改 = 基础值之和 x 乘区倍率
        self._bio.current_state.add_scaled(net, tick.dt_hours)
        self._clamp_state()

        a.clock_hour = (a.clock_hour + dt_hours) % 24.0
        a.elapsed_hours = a.elapsed_hours + dt_hours
        self._reap(tick)

    def _apply(self, inf) -> None:
        """落地一个效果的瞬时声明（挂载那一刻，dt=0）。"""
        inf.aspects.apply_to(self._bio.aspects)
        self._bio.current_state += inf.instant
        inf.instant.zero()
        self._clamp_state()

    def _reap(self, tick: Tick) -> None:
        """到期就摘。终态由效果自己在最后一帧的声明里给出，引擎不替它写状态。"""
        self._effects = [eff for eff in self._effects if eff.alive(self._bio, tick)]

    def _clamp_state(self) -> None:
        s = self._bio.current_state
        for name, dim in self._dimensions.items():
            setattr(s, name, dim.clamp(getattr(s, name)))
