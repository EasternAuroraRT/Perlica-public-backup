"""可复用时间上下文：引擎构造一次，每次更新原地改字段（热路径零分配）。"""
class Tick:
    __slots__ = ("dt", "sim_now", "elapsed", "scale")

    def __init__(self) -> None:
        self.dt = 0.0
        self.sim_now = 0.0
        self.elapsed = 0.0
        self.scale = 1.0

    def set(self, dt: float, sim_now: float, elapsed: float, scale: float) -> None:
        self.dt = dt
        self.sim_now = sim_now
        self.elapsed = elapsed
        self.scale = scale

