from __future__ import annotations
from datetime import datetime
from enum import Enum, unique, auto
from logging import DEBUG
from typing import * # pyright: ignore[reportWildcardImportFromLibrary]
from openai.types.chat import * # pyright: ignore[reportWildcardImportFromLibrary]

from napcat import * # pyright: ignore[reportWildcardImportFromLibrary]
from config import config
import modules.core.env as env
import modules.core.qmessage as qmsg
import modules.core.history as history
from modules.core.chatwindow import ChatWindow
from modules.core.logger import log, UserRestart, log_level

@unique
class EventUrgency(Enum):
    Ignore = 0          # always ignore
    Normal = auto()     # ignored when no-disturbing mode is on
    Important = auto()  # ignored when resting
    Urgent = auto()     # always notify

async def parse_event(event: NapCatEvent, multimodal: bool = False) -> tuple[list[ChatCompletionContentPartParam], EventUrgency]:
    api_call_msg: list[ChatCompletionContentPartParam] = []
    event_urgency: EventUrgency = EventUrgency.Ignore
    async def get_user_nickname(user_id: int) -> str:
        nickname = env.username_list.get(user_id)
        if nickname:
            return nickname
        try:
            nickname = (await env.npclient.get_stranger_info(user_id=str(user_id))).get('nickname', '')
        except Exception as e:
            log.error(f"[events->get_user_nickname] {e}")
            nickname = ''
        return nickname or str(user_id)
    match event:
        case HeartbeatEvent():
            log.info("Heartbeat RCVD")
            prompt = f"You are watching your terminal at {datetime.now().strftime("%m-%d-%H-%M")}. Do anything as you wish."
            api_call_msg.append({'type':'text', 'text': prompt})
            event_urgency = EventUrgency.Important
        case MessageEvent():
            if not event.message:
                event_urgency = EventUrgency.Ignore
            else:
                log.info("Msg RCVD")
                log.debug(str(event.message))
                match event:
                    case PrivateMessageEvent():
                        history.store_private_message(event)
                        prompt = f"Received private message from user `{event.sender.nickname}` (id:{event.sender.user_id}): "
                    case GroupMessageEvent():
                        history.store_group_message(event)
                        prompt = f"Received group message from user `{event.sender.nickname}` (id:{event.sender.user_id}) in group `{event.group_name}` (id:{event.group_id}): "
                    case _:
                        log.error("[events->MessageEvent] Cannot parse MessageEvent")
                        raise RuntimeError("[events->MessageEvent] Cannot parse MessageEvent")
                api_call_msg.append({'type': 'text', 'text':prompt})
                api_call_msg.extend(qmsg.parse_msg_to_list(event.message, multimodal))
                event_urgency = EventUrgency.Normal
        case PokeEvent():  # pyright: ignore[reportGeneralTypeIssues]
            def parse_poke_raw(data, sender_name: str, target_name: str) -> str:
                parts = []
                uid_counter = 0
                for item in data:
                    if 'uid' in item:
                        if uid_counter == 0:
                            parts.append(str(sender_name))
                        elif uid_counter == 1:
                            parts.append(str(target_name))
                        uid_counter += 1
                    elif 'txt' in item:
                        parts.append(item['txt'])
                return ''.join(parts)
            log.info("PokeEvent")
            sender_nickname: str = await get_user_nickname(event.user_id)
            match event:
                case FriendPokeEvent():
                    if(log_level == DEBUG and int(event.sender_id) == config.manager_id):
                        raise UserRestart
                    if event.sender_id != env.self_id:
                        prompt = f"User `{sender_nickname}` double clicked your avatar in the chatbox (You may poke back as well). Interaction msg: {parse_poke_raw(event.raw_info, sender_nickname, '你')}"
                case GroupPokeEvent():
                    target_nickname = await get_user_nickname(event.target_id)
                    prompt = ('Your avatar was' if event.target_id == env.self_id else f"{target_nickname}'s avatar was") + ' double clicked in group ' + f'{event.group_id}' + ' in the chatbox. Interaction msg: ' + parse_poke_raw(event.raw_info, sender_nickname, '你' if event.target_id == env.self_id else target_nickname)
            api_call_msg.append({'type': 'text', 'text': prompt})
            event_urgency = EventUrgency.Normal
        case FriendAddNoticeEvent():  # pyright: ignore[reportGeneralTypeIssues]
            new_friend_name: str = (await env.npclient.get_stranger_info(user_id=str(event.user_id))).get('nickname', '')
            env.chatwindows[ChatWindow.ChatType.Private][str(event.user_id)] = ChatWindow(ChatWindow.ChatType.Private, str(event.user_id), new_friend_name)
            api_call_msg.append({'type': 'text', 'text':f"A new friend (id={event.user_id}, nickname={new_friend_name}) is added."})
            event_urgency = EventUrgency.Normal
        case InputStatusEvent():  # pyright: ignore[reportGeneralTypeIssues]
            event_urgency = EventUrgency.Ignore # [TODO]
        case GroupNameEvent():  # pyright: ignore[reportGeneralTypeIssues]
            log.info("GroupNameEvent")
            api_call_msg.append({'type': 'text', 'text':f"Group `{event.name_new}`(id={event.group_id}) is now named as {event.name_new}."})
            event_urgency = EventUrgency.Normal
        case GroupIncreaseEvent():  # pyright: ignore[reportGeneralTypeIssues]
            log.info("GroupIncreaseEvent")
            if not str(event.group_id) in env.chatwindows[ChatWindow.ChatType.Group]:
                async def get_group_name(group_id) -> str:
                    try:
                        result = (await env.npclient.get_group_info(group_id=group_id)).get('group_name', '$Unknown$')
                    except Exception as e:
                        log.error(f"[events->consume_event->GroupIncreaseEvent] Failed to get gruop info: {e}")
                        result = '$Unknown$'
                    return result
                env.chatwindows[ChatWindow.ChatType.Group][str(event.group_id)] = ChatWindow(ChatWindow.ChatType.Group, str(event.group_id), await get_group_name(event.group_id))
                prompt = f'You are now added to group {event.group_id}.'
            else:
                prompt = f'User {event.user_id} is added to group {event.group_id}. You may welcome him/her. Use tool to get more info.'
            api_call_msg.append({'type': 'text', 'text': prompt})
            event_urgency = EventUrgency.Normal
        case GroupDecreaseEvent():  # pyright: ignore[reportGeneralTypeIssues]
            log.info("GroupDecreaseEvent")
            prompt = f'{"You are" if event.user_id == env.self_id else f"User {event.user_id} is"} removed from group {event.group_id}.'
            if event.sub_type == 'kick_me':
                del env.chatwindows[ChatWindow.ChatType.Group][str(event.group_id)]
            api_call_msg.append({'type': 'text', 'text': prompt})
            event_urgency = EventUrgency.Normal
        case _:
            log.info(f'Event not recognized: {event}')
            event_urgency = EventUrgency.Ignore
    return api_call_msg, event_urgency
