from __future__ import annotations
from typing import * # pyright: ignore[reportWildcardImportFromLibrary]
from openai.types.chat import * # pyright: ignore[reportWildcardImportFromLibrary]
import asyncio
import time
from datetime import datetime

import napcat as np
import modules.core.act as act
import modules.core.env as env
import modules.core.events as events
from modules.core.logger import log


unprocessed_event_msgs: list[ChatCompletionContentPartParam] = []

async def main() -> None:
    await env.init()
    npclient = env.npclient
    biosim = env.biosim_engine
    async for event in npclient:
        should_reply: bool = False
        should_queue: bool = False
        active_mode = env.is_active_time()
        no_disturb_mode = env.no_disturb_mode
        api_call_msg, urgency = await events.parse_event(event)
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
            unprocessed_event_msgs.extend(api_call_msg)
            act.act(unprocessed_event_msgs.copy())
            unprocessed_event_msgs.clear()
        elif should_queue:
            delayed_msg: list[ChatCompletionContentPartParam] = [{'type':'text', 'text': f"The following one notification is sent at time {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}.\n"}]
            delayed_msg.extend(api_call_msg)
            unprocessed_event_msgs.extend(delayed_msg)
            log.debug("New msg queued.")
        elif api_call_msg:
            log.warning(f"Some event is ignored but message is actually generated:\n{str(api_call_msg)}")


if __name__ == "__main__":
    while True:
        try:
            asyncio.run(main())
        except Exception as e:
            log.error(f"[main] {e}")
            time.sleep(1)
