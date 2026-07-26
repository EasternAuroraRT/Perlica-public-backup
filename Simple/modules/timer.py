import time
import threading
import uuid
from typing import Callable, Dict, List, Optional, Any, Tuple

import modules.chat as chat
from modules.logger import log

# 全局状态
_timers: Dict[str, Dict[str, Any]] = {}  # id -> {timer, end_time, duration, callback, args, kwargs}
_lock = threading.Lock()                 # 保护 _timers 的锁


def _run_callback(timer_id: str) -> None:
    # 先取出描述（在锁内）
    with _lock:
        entry = _timers.pop(timer_id, None)  # 直接 pop，返回条目或 None
    # 如果 entry 为 None，说明计时器已被取消或已执行过，可忽略
    if entry is None:
        return
    description = entry.get('description', 'None')
    # 执行用户回调（这里使用了 chat.chat）
    try:
        chat.chat(f"Timer {timer_id} ended. Description: {description}", True)
    except Exception as e:
        log.error(f"[timer->_run_callback] Countdown {timer_id} callback failed: {e}")


def set_timer(duration: float, description: str = '') -> str:
    """
    创建一个倒计时。

    :param duration: 倒计时时长（秒），必须为正数
    :param description: 倒计时描述（可选）
    :return: 该倒计时的唯一标识符（UUID 字符串）
    :raises ValueError: 当 duration <= 0 时抛出
    """
    if duration <= 0:
        log.error("[timer->set_timer] duration must be positive.")
        raise ValueError("duration must be positive.")

    timer_id = str(uuid.uuid4())
    end_time = time.time() + duration

    # 创建 Timer 对象，到期时调用 _run_callback
    timer = threading.Timer(duration, _run_callback, args=(timer_id,))
    timer.daemon = True  # 设置为守护线程，不影响程序退出

    with _lock:
        _timers[timer_id] = {
            'timer': timer,
            'end_time': end_time,
            'duration': duration,
            'description': description
        }

    timer.start()
    return timer_id


def get_timer_remaining_time(timer_id: str) -> Optional[float]:
    """
    查询指定倒计时的剩余时间。

    :param timer_id: 倒计时唯一标识符（UUID 字符串）
    :return: 剩余秒数（浮点数），如果倒计时不存在或已结束则返回 None
    """
    with _lock:
        entry = _timers.get(timer_id)
        if entry is None:
            return None
        remaining = entry['end_time'] - time.time()
        # 因浮点误差，可能出现极小负数，统一处理为0或None
        if remaining < 0:
            # 理论上不会发生，因为结束时立即被移除，但以防万一
            return 0.0
        return remaining


def get_all_timers() -> List[Dict[str, Dict[str, Any]]]:
    """
    获取所有活跃倒计时及其剩余时间和描述。

    :return: 列表，每个元素为 {id: {"remaining": 剩余秒数, "description": 描述}} 的字典
    """
    result = []
    now = time.time()
    with _lock:
        for tid, entry in _timers.items():
            remaining = entry['end_time'] - now
            if remaining < 0:
                remaining = 0.0  # 实际不会出现，因为结束后立即被移除
            result.append({tid: {"remaining": remaining, "description": entry.get('description', 'None')}})
    return result


def cancel_timer_by_id(timer_id: str) -> bool:
    """
    取消指定的倒计时。

    :param timer_id: 倒计时唯一标识符（UUID 字符串）
    :return: 如果成功取消（倒计时存在且尚未触发）返回 True，否则返回 False
    """
    with _lock:
        entry = _timers.pop(timer_id, None)
        if entry is None:
            return False
        # 停止 Timer
        entry['timer'].cancel()
    return True


def cancel_all() -> None:
    """取消所有活跃倒计时（通常用于程序退出前的清理）"""
    with _lock:
        for entry in _timers.values():
            entry['timer'].cancel()
        _timers.clear()