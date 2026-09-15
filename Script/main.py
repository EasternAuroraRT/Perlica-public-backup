from __future__ import annotations
from typing import * # pyright: ignore[reportWildcardImportFromLibrary]
from openai.types.chat import * # pyright: ignore[reportWildcardImportFromLibrary]
import asyncio
import time
from datetime import datetime
import os, sys
import traceback

import napcat as np
from config import config
import modules.core.act as act
import modules.core.env as env
import modules.core.events as events
from modules.core.logger import log, UserRestart
from modules.simulation import biosim
from modules.tools.impl import bio_text


async def main() -> None:
    await env.init()
    npclient = env.npclient
    biosim_engine = env.biosim_engine
    async for event in npclient:
        bio_state_description: ChatCompletionContentPartParam
        bio_state = biosim_engine.get_slice()
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
            case events.EventUrgency.NormalNoQueue:
                should_reply = active_mode and not no_disturb_mode
                should_queue = False
            case events.EventUrgency.Important:
                should_reply = active_mode
                should_queue = True
            case events.EventUrgency.ImportantNoQueue:
                should_reply = active_mode
                should_queue = False
            case events.EventUrgency.Urgent:
                should_reply = True
                should_queue = False
        if should_reply:
            log.info(bio_state_description["text"])
            final_msg: list[ChatCompletionContentPartParam] = []
            final_msg.append(bio_state_description)
            if bio_state.sleep is not biosim.SleepState.AWAKE:
                biosim_engine.add_effect(biosim.WakeEffect())
                final_msg.append({'type': "text", "text": f"You are waken up forcely by following event.\n"})
            final_msg.extend(env.take_pending() + api_call_msg)
            final_msg.append({'type': 'text', 'text': env.get_chatwindow_prompt()})
            act.act(final_msg)
        elif should_queue:
            delayed_msg: list[ChatCompletionContentPartParam] = [{'type':'text', 'text': f"The following one notification is sent at time {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}.\n"}]
            delayed_msg.extend(api_call_msg)
            env.pending_event_msgs.extend(delayed_msg)
            log.debug("New msg queued.")
        elif api_call_msg:
            log.warning(f"Some event is ignored but message is actually generated:\n{str(api_call_msg)}")


def prepare_exit() -> None:
    env.biosim_engine.save_checkpoint()
    env.save_chat_log()
    

if __name__ == "__main__":
    __restart_times = 0
    __max_restart_times = 10
    log.debug("Currently in debug mode. Poke to reload.")
    while True:
        try:
            asyncio.run(main())
        except UserRestart:
            prepare_exit()
            log.warning("Restarting...")
            sys.exit(2)
        except KeyboardInterrupt:
            log.warning("Exit...")
            prepare_exit()
            sys.exit(0)
        except Exception as e:
            log.error(f"[{__file__}] {e}\n{traceback.format_exc()}")
            if __restart_times > __max_restart_times:
                log.fatal(f"[{__file__}] Retrying for over {__max_restart_times} times; Restarting...\n")
                prepare_exit()
                sys.exit(1)
            __restart_times += 1
            time.sleep(1)
