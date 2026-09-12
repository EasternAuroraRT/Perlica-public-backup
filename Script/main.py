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


unprocessed_event_msgs: list[ChatCompletionContentPartParam] = []

async def main() -> None:
    await env.init()
    npclient = env.npclient
    biosim_engine = env.biosim_engine
    async for event in npclient:
        bio_state_description: list[ChatCompletionContentPartParam] = []
        bio_state = biosim_engine.get_state()
        bio_state_description.append({'type': "text", "text": f"Current state: {bio_state.get('sleep', biosim.SleepState.AWAKE).name}.\n"})
        should_reply: bool = False
        should_queue: bool = False
        # active_mode = bio_state.get('sleep', biosim.SleepState.AWAKE) is biosim.SleepState.AWAKE
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
            final_msg: list[ChatCompletionContentPartParam]
            if not bio_state["sleep"] is biosim.SleepState.AWAKE:
                biosim_engine.force_wake()
                bio_state_description.append({'type': "text", "text": f"You are waken up forcely by following event.\n"})
            final_msg = bio_state_description + unprocessed_event_msgs + api_call_msg
            act.act(final_msg)
            unprocessed_event_msgs.clear()
        elif should_queue:
            delayed_msg: list[ChatCompletionContentPartParam] = [{'type':'text', 'text': f"The following one notification is sent at time {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}.\n"}]
            delayed_msg.extend(api_call_msg)
            unprocessed_event_msgs.extend(delayed_msg)
            log.debug("New msg queued.")
        elif api_call_msg:
            log.warning(f"Some event is ignored but message is actually generated:\n{str(api_call_msg)}")


if __name__ == "__main__":
    log.debug("Currently in debug mode. Poke to reload.")
    while True:
        try:
            asyncio.run(main())
        except UserRestart:
            log.warning("Restarting...")
            try:
                os.execv(sys.executable, [sys.executable] + sys.argv)
            except OSError as e:
                log.error(f"[main] execv failed: {e}. Fallback to in-process rerun.")
                time.sleep(1)
        except Exception as e:
            log.error(f"[main] {e}")
            time.sleep(1)
