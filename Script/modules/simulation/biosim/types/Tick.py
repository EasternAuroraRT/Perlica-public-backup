"""可复用时间上下文：引擎构造一次，每次计算原地改字段（热路径零分配）。

字段单位：
    dt_hours       本步时长（模拟小时）
    clock_hour     当前时刻（0~24 的小数小时）
    elapsed_hours  已模拟的总时长（小时）
"""


class Tick:
    __slots__ = ("dt_hours", "clock_hour", "elapsed_hours")

    def __init__(self) -> None:
        self.dt_hours = 0.0
        self.clock_hour = 0.0
        self.elapsed_hours = 0.0

    def set(self, dt_hours: float, clock_hour: float, elapsed_hours: float) -> None:
        self.dt_hours = dt_hours
        self.clock_hour = clock_hour
        self.elapsed_hours = elapsed_hours
