"""示例：推进 + 下命令。文本渲染不在这里——那是消费方自己的工具。"""
from .config import default_config
from .engine import BioSimEngine
from .actions import EatParams, ExerciseParams, SleepParams, WakeParams


def main() -> None:
    engine = BioSimEngine(default_config(), start_hour=8.0)
    engine.advance(4.0)
    print("读数:", engine.observe())
    print("可做:", engine.available_actions())
    for params in (EatParams(portion=0.5), ExerciseParams(intensity=1.0, minutes=10), SleepParams()):
        try:
            engine.act(params)
        except ValueError as exc:
            print("被拒:", exc)
            continue
        engine.advance(0.5)
        print("->", engine.observe())


if __name__ == "__main__":
    main()

