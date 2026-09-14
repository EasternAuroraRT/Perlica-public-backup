"""示例：挂一组常态效果，推进时间，再挂行为效果看读数。"""
from .BasicEffects.physics import EatEffect, ExerciseEffect, SleepEffect, WakeEffect
from .templates import standard


def main() -> None:
    engine = standard(start_hour=8.0)
    engine.advance(4.0)
    print("读数:", engine.get_slice())

    for effect in (EatEffect(portion=0.5),
                   ExerciseEffect(intensity=1.0, minutes=10),
                   SleepEffect()):
        reason = effect.refusal(engine.get_slice())
        if reason is not None:
            print("挂不上", type(effect).__name__, ":", reason)
            continue
        engine.add_effect(effect)
        engine.advance(0.5)
        print(type(effect).__name__, "->", engine.get_slice())

    engine.add_effect(WakeEffect())
    print("醒来:", engine.get_slice().sleep)


if __name__ == "__main__":
    main()
