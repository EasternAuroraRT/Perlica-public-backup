"""bio_text —— 把 biosim 的类型化读数渲染成文本。

这是【工具】，不是 biosim 的核心功能：
    核心只交出一个 Observation（类型化读数）+ 一份动作名列表；
    措辞、词汇、句式、模板全在这里，随便改。
    不想要这一套，就直接自己写一个 render(observation, actions) -> str ——
    核心对文本一无所知，也不依赖这里任何东西。

占位符
    {clock} {time} {elapsed_hours} {sleep_duration} {last_sleep_duration} {sleep_full_duration}
    {sleep} {activity} {hunger} {mood} {glycemia}                     中文标签
    {sleep_raw} {activity_raw} {hunger_raw} {mood_raw} {glycemia_raw}     原始值
    {energy} {fullness} {mood_score} {stress} {glucose}              数值
    {energy_label} {fullness_label} {mood_label} {stress_label}
    {actions}
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping


# ---------- 词汇表：改这里就换词；想绕开就用 {xxx_raw} ----------
LABELS: Mapping[str, Mapping[str, str]] = {
    "sleep": {"awake": "清醒", "dozing": "赖床", "light_sleep": "浅睡",
              "deep_sleep": "深睡", "rem": "REM"},
    "activity": {"rest": "休息", "moderate": "适度活动", "active": "活跃"},
    "hunger": {"full": "饱食", "content": "不饿", "hungry": "饥饿"},
    "mood": {"neutral": "中性", "happy": "愉悦", "irritable": "易怒"},
    "glycemia": {"low": "血糖偏低", "normal": "血糖正常", "high": "血糖偏高"},
}

QUALITY: Mapping[str, tuple[str, str, str]] = {
    "energy": ("疲惫", "平静", "充沛"),
    "fullness": ("饥饿", "尚可", "饱满"),
    "mood": ("低落", "中性", "愉悦"),
    "stress": ("轻松", "适中", "高压"),
    "glucose": ("偏低", "正常", "偏高"),
}

# ---------- 缺省模板 ----------
LINE = (
    "{clock}"
    " | 睡眠:{sleep} 活动:{activity} 饥饿:{hunger} 情绪:{mood}"
    " | 能量:{energy:.0f}/100（{energy_label}）"
    " | 饱腹:{fullness:.0f}/100（{fullness_label}）"
    " | 心情:{mood_score:.0f}/100（{mood_label}）"
    " | 压力:{stress:.0f}/100（{stress_label}）"
    " | 上次睡眠:{last_sleep_duration:.1f}h（睡足需{sleep_full_duration:.0f}h）"
)

# ---------- 渲染 ----------
def _clock(hour: float) -> str:
    """小数小时 -> HH:MM。"""
    whole = int(hour) % 24
    minute = int((hour % 1.0) * 60)
    return "{0:02d}:{1:02d}".format(whole, minute)


def _label(kind: str, member) -> str:
    value = member.value if hasattr(member, "value") else str(member)
    return LABELS.get(kind, {}).get(value, value)


def _qual(key: str, value: float) -> str:
    low, mid, high = QUALITY[key]
    if value < 30:
        return low
    if value < 70:
        return mid
    return high


def fields(observation, actions: Iterable[str] = (), *,
           sleep_full_duration: float = 0.0) -> dict[str, Any]:
    """把读数摊平成占位符表（程序也可以直接取用）。"""
    return {
        "clock": _clock(observation.time),
        "time": observation.time,
        "elapsed_hours": observation.elapsed_hours,
        "sleep_duration": observation.sleep_duration,
        "last_sleep_duration": observation.last_sleep_duration,
        "sleep_full_duration": sleep_full_duration,
        "sleep": _label("sleep", observation.sleep),
        "sleep_raw": observation.sleep.value,
        "activity": _label("activity", observation.activity),
        "activity_raw": observation.activity.value,
        "hunger": _label("hunger", observation.hunger),
        "hunger_raw": observation.hunger.value,
        "mood": _label("mood", observation.mood),
        "mood_raw": observation.mood.value,
        "glycemia": _label("glycemia", observation.glycemia),
        "glycemia_raw": observation.glycemia.value,
        "energy": observation.energy,
        "energy_label": _qual("energy", observation.energy),
        "fullness": observation.fullness,
        "fullness_label": _qual("fullness", observation.fullness),
        "mood_score": observation.mood_score,
        "mood_label": _qual("mood", observation.mood_score),
        "stress": observation.stress,
        "stress_label": _qual("stress", observation.stress),
        "glucose": observation.glucose,
        "glucose_label": _qual("glucose", observation.glucose),
        "actions": " / ".join(actions),
    }


def fill(template: str, observation, actions: Iterable[str] = (), *,
         sleep_full_duration: float = 0.0) -> str:
    """用读数填任意模板。"""
    return template.format(**fields(observation, actions,
                                    sleep_full_duration=sleep_full_duration))


def state_text(engine, template: str | None = None) -> str:
    """一行状态摘要。engine 只被用来取读数、动作和配置——核心不认识这里。"""
    return fill(template or LINE, engine.observe(), engine.available_actions(),
                sleep_full_duration=engine.cfg.sleep_full_duration)


