from __future__ import annotations
from typing import * # pyright: ignore[reportWildcardImportFromLibrary]
from openai.types.chat import * # pyright: ignore[reportWildcardImportFromLibrary]
import asyncio
import time
from datetime import datetime
import os, sys

import napcat as np
from config import config
import modules.core.act as act
import modules.core.env as env
import modules.core.events as events
from modules.core.logger import log, log_level, logging, UserRestart
from modules.simulation import biosim
from modules.tools.impl import bio_text


async def main() -> None:
    await env.init()
    npclient = env.npclient
    biosim_engine = env.biosim_engine
    async for event in npclient:
        bio_state_description: ChatCompletionContentPartParam
        bio_state = biosim_engine.observe()
        bio_state_description = {'type': "text", "text": f"Current state: \n{bio_text.state_text(biosim_engine)}\n"}
        should_reply: bool = False
        should_queue: bool = False
        active_mode = env.is_active_time()
        no_disturb_mode = env.no_disturb_mode
        api_call_msg, urgency = await events.parse_event(event, config.model_config[config.using_model].is_multimodal)
        match urgency:
            case events.EventUrgency.Ignore:
                should_reply = False
                should_queue = False
            case events.EventUrgency.Normal:
                should_reply = active_mode and not no_disturb_mode
                should_queue = True
            case events.EventUrgency.Important:
                should_reply = active_mode
                should_queue = True
            case events.EventUrgency.Urgent:
                should_reply = True
                should_queue = False
        if should_reply:
            log.info(bio_state_description["text"])
            final_msg: list[ChatCompletionContentPartParam] = []
            final_msg.append(bio_state_description)
            if bio_state.sleep is not biosim.SleepState.AWAKE:
                biosim_engine.interrupt("sleep", by=biosim.WakeSource.EXTERNAL)
                final_msg.append({'type': "text", "text": f"You are waken up forcely by following event.\n"})
            final_msg.extend(env.take_pending() + api_call_msg)
            act.act(final_msg)
        elif should_queue:
            delayed_msg: list[ChatCompletionContentPartParam] = [{'type':'text', 'text': f"The following one notification is sent at time {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}.\n"}]
            delayed_msg.extend(api_call_msg)
            env.pending_event_msgs.extend(delayed_msg)
            log.debug("New msg queued.")
        elif api_call_msg:
            log.warning(f"Some event is ignored but message is actually generated:\n{str(api_call_msg)}")


if __name__ == "__main__":
    import signal

    def _graceful_exit(signum, _frame) -> None:
        # 主动结束 (Ctrl+C / SIGTERM): 退出码 0, 外面的 supervisor 不要重启
        log.info(f"Received signal {signum}. Bye.")
        sys.exit(0)

    signal.signal(signal.SIGINT, _graceful_exit)
    signal.signal(signal.SIGTERM, _graceful_exit)

    log.debug("Currently in debug mode. Poke to reload.")
    while True:
        try:
            asyncio.run(main())
        except UserRestart:
            log.warning("Restarting...")
            try:
                os.execv(sys.executable, [sys.executable] + sys.argv)
            except OSError as e:
                # 进程内已经加载不到新代码了, 只能非 0 退出, 让 supervisor 重新拉一个进程
                log.error(f"[main] execv failed: {e}. Exiting for the supervisor to restart.")
                sys.exit(1)
        except Exception as e:
            log.error(f"[main] {e}")
            time.sleep(1)
