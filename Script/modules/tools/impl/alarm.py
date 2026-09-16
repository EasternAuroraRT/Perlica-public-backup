"""闹钟：once / daily / weekly，持久化到 JSON，进程启动时重新调度并续上。

要点：
- 进程启动时应调用一次 `ensure_loaded()`（import 本模块也会自动跑一次），
  别等模型碰了 alarm 工具才加载，否则持久化的闹钟根本没被调度。
- 过期的 once 在加载时丢弃并记日志，不会补触发。
- `set_alarm` 用 once 设一个今天已过的时刻，会顺延到明天该时刻。
"""
from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal, TypedDict, cast

from openai.types.chat import ChatCompletionContentPartParam

import modules.core.act as act
from modules.core.logger import log


# ==================== 类型 ====================
LoopMode = Literal["once", "daily", "weekly"]


class AlarmInfo(TypedDict):
    """运行时的一条闹钟。"""
    time: datetime
    loop: LoopMode
    about: str


class _AlarmFileEntry(TypedDict):
    """落盘时的一条闹钟（time 序列化成字符串）。"""
    time: str
    loop: LoopMode
    about: str


# ==================== 配置 ====================
ALARM_FILE = "working_cache/alarms.json"          # 持久化文件路径，可修改


# ==================== 内部状态 ====================
_alarms: dict[str, AlarmInfo] = {}          # id (UUID) -> 闹钟信息
_timers: dict[str, threading.Timer] = {}    # id (UUID) -> Timer 对象
_lock = threading.RLock()                   # 工具线程和 Timer 回调线程都会碰上面两个字典
_loaded = False                             # ensure_loaded() 的幂等标记

_LOOP_MODES: frozenset[str] = frozenset(("once", "daily", "weekly"))


def _parse_loop(value: object) -> LoopMode:
    """把外部传入的循环模式收窄成 LoopMode，非法则抛 ValueError。"""
    if not isinstance(value, str) or value not in _LOOP_MODES:
        raise ValueError(f"无效的循环模式: {value!r}")
    return cast(LoopMode, value)


def _parse_alarm(raw: object) -> AlarmInfo | None:
    """把 JSON 里的一条记录解析成 AlarmInfo，解析不了返回 None。"""
    if not isinstance(raw, dict):
        return None
    record = cast(dict[str, object], raw)
    try:
        time = record["time"]
        loop = _parse_loop(record["loop"])
        about = record["about"]
    except (KeyError, ValueError):
        return None
    if not isinstance(time, str) or not isinstance(about, str):
        return None
    try:
        dt = datetime.fromisoformat(time)
    except ValueError:
        return None
    return {"time": dt, "loop": loop, "about": about}


# ==================== 核心回调（用户可重写） ====================
def on_alarm_triggered(alarm_id: str) -> None:
    with _lock:
        info = _alarms.get(alarm_id)
    about = info["about"] if info is not None else "None"
    log.info(f"Alarm {alarm_id} is ringing!\nDescription: {about}")
    # 闹钟把角色从睡眠里叫醒：睡眠是少有的可被外界打断的内在行为
    pending: list[ChatCompletionContentPartParam] = []
    try:
        import modules.core.env as env
        from modules.simulation import biosim
        env.biosim_engine.add_effect(biosim.WakeEffect())
        pending = env.take_pending()  # 睡着期间积压的消息，一并交给它
    except Exception as e:
        log.error(f"[alarm->on_alarm_triggered->wake] {e}")
    ring: ChatCompletionContentPartParam = {"type": "text", "text": f"Alarm {alarm_id} is ringing! Description: {about}"}
    act.act(pending + [ring])


# ==================== 加载 / 保存 ====================
def ensure_loaded() -> None:
    """幂等：首次调用时从磁盘装载并重新调度，之后是空操作。

    进程启动时显式调一次；import 本模块时也会自动触发。
    """
    global _loaded
    with _lock:
        if _loaded:
            return
        _loaded = True
    _load_alarms()


def _load_alarms() -> None:
    """从 JSON 文件载入闹钟数据并重新调度；过期的 once 丢弃。"""
    global _alarms
    if not Path(ALARM_FILE).exists():
        log.warning(f"[{__file__}->_load_alarms] Cannot find data `{Path(ALARM_FILE).absolute()}`")
        with _lock:
            _alarms = {}
        return

    try:
        with open(ALARM_FILE, "r", encoding="utf-8") as f:
            raw: object = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        log.error(f"[alarm->_load_alarms->with open] {e}")
        with _lock:
            _alarms = {}
        return

    if not isinstance(raw, dict):
        log.error(f"[alarm->_load_alarms] `{ALARM_FILE}` 顶层不是对象, 已忽略")
        with _lock:
            _alarms = {}
        return

    now = datetime.now()
    loaded: dict[str, AlarmInfo] = {}
    dropped = False
    for alarm_id, entry in cast(dict[object, object], raw).items():
        if not isinstance(alarm_id, str):
            continue
        info = _parse_alarm(entry)
        if info is None:
            log.error(f"[alarm->_load_alarms] 跳过无法解析的闹钟 `{alarm_id}`")
            continue
        if info["loop"] == "once" and info["time"] <= now:
            log.warning(
                f"[alarm->_load_alarms] once 闹钟 `{alarm_id}` 已过期, 丢弃: "
                f"{info['time'].isoformat()} ({info['about']})"
            )
            dropped = True
            continue
        loaded[alarm_id] = info
        log.info(f"Alarm {alarm_id} initialized.\nTime: {info['time'].isoformat()}\nLoop: {info['loop']}\nDescription: {info['about']}")

    with _lock:
        _alarms = loaded
        for aid in list(_alarms.keys()):
            try:
                _schedule_alarm(aid)
            except Exception as e:
                log.error(f"[alarm->_load_alarms->schedule] `{aid}`: {e}")
        if dropped:
            _save_alarms()


def _save_alarms() -> None:
    """将当前闹钟数据保存到 JSON 文件"""
    with _lock:
        data: dict[str, _AlarmFileEntry] = {}
        for aid, info in _alarms.items():
            entry: _AlarmFileEntry = {
                "time": info["time"].isoformat(),
                "loop": info["loop"],
                "about": info["about"],
            }
            data[aid] = entry
    Path(ALARM_FILE).parent.mkdir(parents=True, exist_ok=True)
    with open(ALARM_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# ==================== 调度引擎 ====================
def _schedule_alarm(alarm_id: str) -> None:
    """计算闹钟的下次触发时间并启动定时器。过期的 once 直接丢弃。"""
    with _lock:
        info = _alarms.get(alarm_id)
        if info is None:
            return

        dt_base = info["time"]
        loop = info["loop"]
        now = datetime.now()
        next_time: datetime

        # 计算下次触发时间
        if loop == "once":
            if dt_base <= now:
                log.warning(f"[alarm->_schedule_alarm] once 闹钟 `{alarm_id}` 已过期, 丢弃")
                _alarms.pop(alarm_id, None)
                return
            next_time = dt_base
        elif loop == "daily":
            # 使用基准时间中的时刻，日期取今天
            target = dt_base.replace(year=now.year, month=now.month, day=now.day)
            if target <= now:
                target += timedelta(days=1)
            next_time = target
        else:  # weekly
            # 使用基准时间中的星期和时刻
            days_ahead = (dt_base.weekday() - now.weekday()) % 7
            if days_ahead == 0 and dt_base.time() <= now.time():
                days_ahead = 7
            target_date = now + timedelta(days=days_ahead)
            next_time = datetime.combine(target_date, dt_base.time())

        # 计算延迟秒数；Timer 负责在另一个线程回调，避免同步重入 act
        delta = max(0.0, (next_time - now).total_seconds())

        timer = threading.Timer(delta, _trigger_alarm, args=[alarm_id])
        timer.daemon = True
        timer.start()
        _timers[alarm_id] = timer


def _trigger_alarm(alarm_id: str) -> None:
    """定时器回调：触发闹钟，并根据循环模式处理后续"""
    # 移除定时器引用
    with _lock:
        _timers.pop(alarm_id, None)

    # 调用用户回调；无论成败都要继续处理后续，否则 daily/weekly 会静默消失
    try:
        on_alarm_triggered(alarm_id)
    except Exception as e:
        log.error(f"[alarm->_trigger_alarm->on_alarm_triggered] `{alarm_id}`: {e}")

    with _lock:
        info = _alarms.get(alarm_id)
        if info is None:
            return
        if info["loop"] == "once":
            # 一次性闹钟触发后删除
            del _alarms[alarm_id]
            _save_alarms()
        else:  # daily / weekly
            # 重新调度下一次
            _schedule_alarm(alarm_id)


def _parse_hhmm(time_str: str) -> tuple[int, int]:
    """解析 hhmm 格式字符串，返回 (小时, 分钟)，出错抛出 ValueError"""
    if not time_str.isdigit() or len(time_str) != 4:
        raise ValueError("时间格式必须为4位数字hhmm，例如 '0830'")
    hour = int(time_str[:2])
    minute = int(time_str[2:])
    if not (0 <= hour < 24 and 0 <= minute < 60):
        raise ValueError("小时或分钟越界，小时 0-23，分钟 0-59")
    return hour, minute


# ==================== 公共 API ====================
def set_alarm(time: str, loop: str, about: str) -> str:
    """
    创建闹钟。

    Args:
        time: 闹钟时间，格式为 'hhmm'，例如 '0830' 表示 08:30。
        loop: 循环模式，'once' / 'daily' / 'weekly'（其他值抛 ValueError）。
              - once: 下一次该时刻（今天已过则顺延到明天），只响一次。
              - daily: 每天同一时刻触发。
              - weekly: 每周同一天（即调用此函数时的星期几）触发。
        about: 文本描述。

    Returns:
        新闹钟的唯一标识符（UUID 字符串）。

    Raises:
        ValueError: 时间字符串格式或数值无效，或循环模式非法。
    """
    hour, minute = _parse_hhmm(time)
    mode = _parse_loop(loop)
    now = datetime.now()

    if mode == "once":
        dt = datetime(now.year, now.month, now.day, hour, minute, 0)
        if dt <= now:
            # 今天这个点已经过了：顺延到明天，别立刻炸
            dt += timedelta(days=1)
    elif mode == "daily":
        # 使用固定日期（2000-01-01）仅保存时刻，调度时忽略日期部分
        dt = datetime(2000, 1, 1, hour, minute, 0)
    else:  # weekly
        # 使用当前日期，保留星期信息
        dt = datetime(now.year, now.month, now.day, hour, minute, 0)

    # 生成唯一 UUID
    alarm_id = str(uuid.uuid4())

    record: AlarmInfo = {
        "time": dt,
        "loop": mode,
        "about": about,
    }
    with _lock:
        _alarms[alarm_id] = record
        _schedule_alarm(alarm_id)
        _save_alarms()
    return alarm_id


def get_alarm(alarm_id: str) -> AlarmInfo | None:
    """
    查询指定闹钟的信息。

    Args:
        alarm_id: 闹钟唯一标识符（UUID 字符串）。

    Returns:
        闹钟信息 {'time': datetime, 'loop': LoopMode, 'about': str}，不存在则返回 None。
    """
    with _lock:
        info = _alarms.get(alarm_id)
        if info is None:
            return None
        return {"time": info["time"], "loop": info["loop"], "about": info["about"]}


def get_all_alarms() -> list[dict[str, AlarmInfo]]:
    """
    获取所有闹钟的列表。

    Returns:
        列表，每个元素为 {id: AlarmInfo} 形式的字典，其中 id 为 UUID 字符串。
    """
    with _lock:
        result: list[dict[str, AlarmInfo]] = []
        for aid, info in _alarms.items():
            snapshot: AlarmInfo = {"time": info["time"], "loop": info["loop"], "about": info["about"]}
            result.append({aid: snapshot})
        return result


def cancel_alarm(alarm_id: str) -> None:
    """
    取消指定编号的闹钟。

    Args:
        alarm_id: 要取消的闹钟唯一标识符（UUID 字符串）。
    """
    with _lock:
        if alarm_id not in _alarms:
            raise RuntimeError(f"Cannot find alarm with id {alarm_id}.")

        # 取消定时器
        timer = _timers.pop(alarm_id, None)
        if timer is not None:
            timer.cancel()

        # 删除数据并保存
        del _alarms[alarm_id]
        _save_alarms()


# ==================== 模块初始化 ====================
ensure_loaded()
