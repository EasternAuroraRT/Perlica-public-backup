from __future__ import annotations
from typing import * # pyright: ignore[reportWildcardImportFromLibrary]
import asyncio
import time
from datetime import datetime

import napcat as np
import modules.chat as chat
import modules.env as env
import modules.events as events
from modules.logger import log


async def main() -> None:
    await env.init()
    npclient = env.npclient
    async for event in npclient:
        api_call_msg = await events.consume_event(event)
        if api_call_msg:
            chat.chat(api_call_msg)


if __name__ == "__main__":
    while True:
        try:
            asyncio.run(main())
        except Exception as e:
            log.error(f"[main] {e}")
            time.sleep(1)
