"""引擎概念枚举：外在/内在动作的控制性质，与唤醒来源。"""
from enum import Enum


class ControlKind(Enum):
    VOLITIONAL = "volitional"     # 外在：可主观启动/停止
    AUTONOMIC = "autonomic"       # 内在：触发后自行运转


class WakeSource(Enum):
    SELF = "self"
    ALARM = "alarm"
    EXTERNAL = "external"
    NOISE = "noise"

