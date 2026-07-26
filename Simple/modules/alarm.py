import json
import os
import threading
import uuid
from datetime import datetime, timedelta
from typing import Dict, List, Literal, Optional, Union

import modules.chat as chat
from modules.logger import log

# ==================== 配置 ====================
ALARM_FILE = "working_cache/alarms.json"          # 持久化文件路径，可修改


# ==================== 内部状态 ====================
_alarms: Dict[str, Dict] = {}       # id (UUID) -> {time: datetime, loop: str, about: str}
_timers: Dict[str, threading.Timer] = {}   # id (UUID) -> Timer 对象


# ==================== 核心回调（用户可重写） ====================
def on_alarm_triggered(alarm_id: str) -> None:
    about = _alarms.get(alarm_id, {}).get('about', 'None')
    log.info(f"Alarm {alarm_id} is ringing!\nDescription: {about}")
    chat.chat(f"Alarm {alarm_id} is ringing! Description: {about}")


# ==================== 持久化 ====================
def _load_alarms() -> None:
    """从 JSON 文件载入闹钟数据并重新调度"""
    global _alarms
    if not os.path.exists(ALARM_FILE):
        _alarms = {}
        return

    try:
        with open(ALARM_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        log.error(f"[alarm->_load_alarms->with open] {e}")
        _alarms = {}
        return

    # data 的键已经是字符串（UUID），直接使用
    for alarm_id, info in data.items():
        try:
            dt = datetime.fromisoformat(info['time'])
            _alarms[alarm_id] = {
                'time': dt,
                'loop': info['loop'],
                'about': info['about'],
            }
            log.info(f"Alarm {alarm_id} initialized.\nLoop: {info['loop']}\nDescription: {info['about']}\n")
        except (KeyError, ValueError) as e:
            log.error(f"[alarm->_load_alarms->for loop] {e}")
            continue

    # 重新调度所有载入的闹钟
    for aid in list(_alarms.keys()):
        _schedule_alarm(aid)


def _save_alarms() -> None:
    """将当前闹钟数据保存到 JSON 文件"""
    data = {}
    for aid, info in _alarms.items():
        data[aid] = {  # aid 已是字符串 UUID
            'time': info['time'].isoformat(),
            'loop': info['loop'],
            'about': info['about'],
        }
    with open(ALARM_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# ==================== 调度引擎 ====================
def _schedule_alarm(alarm_id: str) -> None:
    """
    计算闹钟的下次触发时间并启动定时器。
    若闹钟已过期（仅 once 模式），则立即触发并删除。
    """
    info = _alarms.get(alarm_id)
    if not info:
        return

    dt_base = info['time']
    loop = info['loop']
    now = datetime.now()

    # 计算下次触发时间
    if loop == 'once':
        if dt_base <= now:
            # 已过期 → 立即触发并删除
            _trigger_alarm(alarm_id)
            return
        next_time = dt_base

    elif loop == 'daily':
        # 使用基准时间中的时刻，日期取今天
        target = dt_base.replace(year=now.year, month=now.month, day=now.day)
        if target <= now:
            target += timedelta(days=1)
        next_time = target

    elif loop == 'weekly':
        # 使用基准时间中的星期和时刻
        days_ahead = (dt_base.weekday() - now.weekday()) % 7
        if days_ahead == 0 and dt_base.time() <= now.time():
            days_ahead = 7
        target_date = now + timedelta(days=days_ahead)
        next_time = datetime.combine(target_date, dt_base.time())

    else:
        raise ValueError(f"无效的循环模式: {loop}")

    # 计算延迟秒数
    delta = (next_time - now).total_seconds()
    if delta < 0:
        delta = 0

    # 创建并启动定时器
    timer = threading.Timer(delta, _trigger_alarm, args=[alarm_id])
    timer.daemon = True
    timer.start()
    _timers[alarm_id] = timer


def _trigger_alarm(alarm_id: str) -> None:
    """定时器回调：触发闹钟，并根据循环模式处理后续"""
    # 移除定时器引用
    _timers.pop(alarm_id, None)

    # 调用用户回调
    on_alarm_triggered(alarm_id)

    # 获取闹钟信息
    info = _alarms.get(alarm_id)
    if not info:
        return

    loop = info['loop']
    if loop == 'once':
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
def set_alarm(time: str, loop: Literal['once', 'daily', 'weekly'], about: str) -> str:
    """
    创建闹钟。

    Args:
        time: 闹钟时间，格式为 'hhmm'，例如 '0830' 表示 08:30。
        loop: 循环模式，'once' / 'daily' / 'weekly'。
              - once: 使用今天的日期 + 给定时间，若已过则立即触发。
              - daily: 每天同一时刻触发。
              - weekly: 每周同一天（即调用此函数时的星期几）触发。
        about: 文本描述。

    Returns:
        新闹钟的唯一标识符（UUID 字符串）。

    Raises:
        ValueError: 时间字符串格式或数值无效。
    """
    hour, minute = _parse_hhmm(time)
    now = datetime.now()

    if loop == 'once':
        dt = datetime(now.year, now.month, now.day, hour, minute, 0)
    elif loop == 'daily':
        # 使用固定日期（2000-01-01）仅保存时刻，调度时忽略日期部分
        dt = datetime(2000, 1, 1, hour, minute, 0)
    elif loop == 'weekly':
        # 使用当前日期，保留星期信息
        dt = datetime(now.year, now.month, now.day, hour, minute, 0)
    else:
        raise ValueError(f"无效的循环模式: {loop}")

    # 生成唯一 UUID
    alarm_id = str(uuid.uuid4())

    _alarms[alarm_id] = {
        'time': dt,
        'loop': loop,
        'about': about,
    }
    _schedule_alarm(alarm_id)
    _save_alarms()
    return alarm_id


def get_alarm(alarm_id: str) -> Optional[Dict[str, Union[datetime, str]]]:
    """
    查询指定闹钟的信息。

    Args:
        alarm_id: 闹钟唯一标识符（UUID 字符串）。

    Returns:
        字典 {'time': datetime, 'loop': str, 'about': str}，若不存在则返回 None。
    """
    info = _alarms.get(alarm_id)
    if not info:
        return None
    return {
        'time': info['time'],
        'loop': info['loop'],
        'about': info['about'],
    }


def get_all_alarms() -> List[Dict[str, Dict[str, Union[datetime, str]]]]:
    """
    获取所有闹钟的列表。

    Returns:
        列表，每个元素为 {id: {'time': ..., 'loop': ..., 'about': ...}} 形式的字典，
        其中 id 为 UUID 字符串。
    """
    result = []
    for aid, info in _alarms.items():
        result.append({
            aid: {
                'time': info['time'],
                'loop': info['loop'],
                'about': info['about'],
            }
        })
    return result


def cancel_alarm(alarm_id: str) -> None:
    """
    取消指定编号的闹钟。

    Args:
        alarm_id: 要取消的闹钟唯一标识符（UUID 字符串）。
    """
    if alarm_id not in _alarms:
        return

    # 取消定时器
    if alarm_id in _timers:
        _timers[alarm_id].cancel()
        del _timers[alarm_id]

    # 删除数据并保存
    del _alarms[alarm_id]
    _save_alarms()


# ==================== 模块初始化 ====================
_load_alarms()