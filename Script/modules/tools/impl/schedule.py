import json
import os
import threading
import uuid
from datetime import datetime
from typing import * # pyright: ignore[reportWildcardImportFromLibrary]

import modules.core.act as act
from modules.core.logger import log

# ==================== 配置 ====================
SCHEDULE_FILE = "working_cache/schedules.json"    # 持久化文件路径，可修改

# 支持的时间格式（均为精确到分钟的完整日期时间）
_TIME_FORMATS = [
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d %H:%M:%S",
    "%Y/%m/%d %H:%M",
    "%Y/%m/%d %H:%M:%S",
    "%Y-%m-%dT%H:%M",
    "%Y-%m-%dT%H:%M:%S",
    "%Y年%m月%d日 %H:%M",
]


# ==================== 内部状态 ====================
_schedules: Dict[str, Dict] = {}                 # id (UUID) -> {time: datetime, about: str}
_timers: Dict[str, threading.Timer] = {}         # id (UUID) -> Timer 对象


# ==================== 核心回调（用户可重写） ====================
def on_schedule_triggered(schedule_id: str) -> None:
    about = _schedules.get(schedule_id, {}).get('about', 'None')
    log.info(f"Schedule {schedule_id} is due!\nDescription: {about}")
    act.act(f"Schedule {schedule_id} is due! Description: {about}")


# ==================== 持久化 ====================
def _load_schedules() -> None:
    """从 JSON 文件载入日程数据并重新调度"""
    global _schedules
    if not os.path.exists(SCHEDULE_FILE):
        _schedules = {}
        return

    try:
        with open(SCHEDULE_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        log.error(f"[schedule->_load_schedules->with open] {e}")
        _schedules = {}
        return

    for schedule_id, info in data.items():
        try:
            dt = datetime.fromisoformat(info['time'])
            _schedules[schedule_id] = {
                'time': dt,
                'about': info['about'],
            }
            log.info(f"Schedule {schedule_id} initialized.\nTime: {info['time']}\nDescription: {info['about']}")
        except (KeyError, ValueError) as e:
            log.error(f"[schedule->_load_schedules->for loop] {e}")
            continue

    for sid in list(_schedules.keys()):
        _schedule_schedule(sid)


def _save_schedules() -> None:
    """将当前日程数据保存到 JSON 文件"""
    os.makedirs(os.path.dirname(SCHEDULE_FILE), exist_ok=True)
    data = {}
    for sid, info in _schedules.items():
        data[sid] = {
            'time': info['time'].isoformat(),
            'about': info['about'],
        }
    with open(SCHEDULE_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# ==================== 调度引擎 ====================
def _schedule_schedule(schedule_id: str) -> None:
    """计算日程的剩余时间并启动定时器。若已过期则立即触发并删除。"""
    info = _schedules.get(schedule_id)
    if not info:
        return

    now = datetime.now()
    delta = (info['time'] - now).total_seconds()
    if delta <= 0:
        # 已过期 → 立即触发并删除
        _trigger_schedule(schedule_id)
        return

    timer = threading.Timer(delta, _trigger_schedule, args=[schedule_id])
    timer.daemon = True
    timer.start()
    _timers[schedule_id] = timer


def _trigger_schedule(schedule_id: str) -> None:
    """定时器回调：触发日程并删除"""
    _timers.pop(schedule_id, None)
    on_schedule_triggered(schedule_id)
    if schedule_id in _schedules:
        del _schedules[schedule_id]
        _save_schedules()


def _parse_datetime(time_str: str) -> datetime:
    """解析完整日期时间字符串，返回 datetime，出错抛出 ValueError"""
    if not isinstance(time_str, str) or not time_str.strip():
        raise ValueError("时间参数不能为空")
    time_str = time_str.strip()
    for fmt in _TIME_FORMATS:
        try:
            return datetime.strptime(time_str, fmt)
        except ValueError:
            continue
    raise ValueError("时间格式无法识别，请使用形如 'YYYY-MM-DD HH:MM' 的完整日期时间")


# ==================== 公共 API ====================
def set_schedule(time: str, about: str) -> str:
    """
    创建一个日程提醒（一次性，精确到分钟）。

    Args:
        time: 触发时间，格式如 'YYYY-MM-DD HH:MM'（支持多种常见日期时间格式）。
        about: 文本描述。

    Returns:
        新日程的唯一标识符（UUID 字符串）。

    Raises:
        ValueError: 时间格式无效，或时间已过。
    """
    dt = _parse_datetime(time)
    if dt <= datetime.now():
        raise ValueError(f"时间 {time} 已过，无法设置日程")

    schedule_id = str(uuid.uuid4())
    _schedules[schedule_id] = {
        'time': dt,
        'about': about,
    }
    _schedule_schedule(schedule_id)
    _save_schedules()
    return schedule_id


def get_schedule(schedule_id: str) -> Optional[Dict[str, Union[datetime, str]]]:
    """
    查询指定日程的信息。

    Returns:
        字典 {'time': datetime, 'about': str}，若不存在则返回 None。
    """
    info = _schedules.get(schedule_id)
    if not info:
        return None
    return {
        'time': info['time'],
        'about': info['about'],
    }


def get_all_schedules() -> List[Dict[str, Dict[str, Union[datetime, str]]]]:
    """
    获取所有日程的列表。

    Returns:
        列表，每个元素为 {id: {'time': ..., 'about': ...}} 形式的字典。
    """
    result = []
    for sid, info in _schedules.items():
        result.append({
            sid: {
                'time': info['time'],
                'about': info['about'],
            }
        })
    return result


def cancel_schedule(schedule_id: str) -> None:
    """
    取消指定编号的日程。

    Args:
        schedule_id: 要取消的日程唯一标识符（UUID 字符串）。
    """
    if schedule_id not in _schedules:
        return

    if schedule_id in _timers:
        _timers[schedule_id].cancel()
        del _timers[schedule_id]

    del _schedules[schedule_id]
    _save_schedules()


# ==================== 模块初始化 ====================
_load_schedules()
