from __future__ import annotations
import json
from pathlib import Path
from datetime import datetime
from typing import Optional

import napcat as np
import modules.qmessage as qmsg
import modules.env as env
from modules.logger import log

HISTORY_DIR = Path(__file__).parent.parent/'history'
GROUP_DIR = HISTORY_DIR / "group"
PRIVATE_DIR = HISTORY_DIR / "private"

def _ensure_dirs():
    GROUP_DIR.mkdir(parents=True, exist_ok=True)
    PRIVATE_DIR.mkdir(parents=True, exist_ok=True)


INDEX_PATH = HISTORY_DIR / "msg_index.json"

def _load_index() -> dict[int, dict[str, str]]:
    """加载索引，返回 dict[msg_id, {chat_type, target_id}]"""
    if not INDEX_PATH.exists():
        return {}
    try:
        with open(INDEX_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
            # 将 JSON 的字符串 key 转回 int
            return {int(k): v for k, v in data.items()}
    except (json.JSONDecodeError, OSError):
        return {}


def _save_index(index: dict[int, dict[str, str]]):
    """保存索引（key 转字符串）"""
    _ensure_dirs()
    with open(INDEX_PATH, 'w', encoding='utf-8') as f:
        json.dump({str(k): v for k, v in index.items()}, f, ensure_ascii=False, indent=2)


def _format_time() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# -------------------------------------------------------------------
# 序列化：存 type + data；无法获取时存 raw
# -------------------------------------------------------------------

def _serialize_message(msg: np.Message | np.UnknownMessageSegment) -> dict:
    msg_type = getattr(msg, 'type', None)
    msg_data = getattr(msg, 'data', None)
    if msg_type is not None and msg_data is not None:
        return {"type": msg_type, "data": msg_data}
    return {"raw": str(msg)}


# -------------------------------------------------------------------
# 重建：type+data 直接用构造函数；raw 作兼容回退
# -------------------------------------------------------------------

def _reconstruct_segments(msg_list: list[dict]) -> tuple:
    segments = []
    for seg in msg_list:
        msg_type = seg.get("type")
        msg_data = seg.get("data")
        if msg_type is not None and msg_data is not None:
            cls = getattr(np, msg_type, None)
            if cls is not None:
                try:
                    obj = cls(**msg_data) if isinstance(msg_data, dict) else cls(msg_data)
                    segments.append(obj)
                    continue
                except Exception as e:
                    log.error(f"[history->_reconstruct_segments->msg_type] {e}")
                    pass
        # 回退：兼容旧的 raw 格式
        raw = seg.get("raw", "")
        if raw:
            try:
                obj = eval(f"np.{raw}", {"np": np})
                if isinstance(obj, np.MessageSegment):
                    segments.append(obj)
                    continue
            except Exception as e:
                log.error(f"[history->_reconstruct_segments->raw] {e}")
                pass
        segments.append(np.Text(text=raw))
    return tuple(segments)


# -------------------------------------------------------------------
# 内部读写
# -------------------------------------------------------------------

def _load_history(filepath: Path) -> list[dict]:
    if not filepath.exists():
        return []
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError) as e:
        log.error(f"[history->_load_history] {e}")
        return []


def _save_history(filepath: Path, messages: list[dict]):
    _ensure_dirs()
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(messages, f, ensure_ascii=False, indent=2)


def _get_filepath_by_msg_id(msg_id: int) -> Optional[Path]:
    """通过索引快速定位消息所在文件，找不到返回 None"""
    index = _load_index()
    entry = index.get(msg_id)
    if not entry:
        return None
    chat_type = entry["chat_type"]
    target_id = entry["target_id"]
    if chat_type == "group":
        return GROUP_DIR / f"{target_id}.json"
    else:
        return PRIVATE_DIR / f"{target_id}.json"


# -------------------------------------------------------------------
# 接口 1 / 2
# -------------------------------------------------------------------

def store_group_message(event: np.GroupMessageEvent):
    group_id = event.group_id
    group_name: str = getattr(event, 'group_name', str(group_id))
    sender = event.sender
    sender_id: int | str = sender.user_id
    msg_id: int = event.message_id

    message_data = [_serialize_message(seg) for seg in event.message]
    _store_entry(
        chat_type="group",
        target_id=str(group_id),
        target_name=group_name,
        user_id=str(sender_id),
        nickname=sender.nickname,
        message_data=message_data,
        msg_id=msg_id,
    )


def store_private_message(event: np.PrivateMessageEvent):
    sender = event.sender
    sender_id: int | str = sender.user_id
    msg_id: int = event.message_id
    message_data = [_serialize_message(seg) for seg in event.message]
    _store_entry(
        chat_type="private",
        target_id=str(sender_id),
        target_name=sender.nickname,
        user_id=str(sender_id),
        nickname=sender.nickname,
        message_data=message_data,
        msg_id=msg_id,
    )


# -------------------------------------------------------------------
# 接口 3
# -------------------------------------------------------------------

def store_message(
    chat_type: str,
    target_id: int | str,
    message_content: list[np.Message | np.UnknownMessageSegment],
    *,
    sender_id: Optional[int | str] = None,
    sender_nickname: Optional[str] = None,
    target_name: Optional[str] = None,
    msg_id: int = 0,
):
    _ensure_dirs()

    target_id_str = str(target_id)

    if chat_type == "private":
        sid = str(sender_id) if sender_id is not None else target_id_str
        nick = sender_nickname or target_name or sid
    elif chat_type == "group":
        sid = str(sender_id) if sender_id else "0"
        nick = sender_nickname or sid
    else:
        log.error(f"[history->store_message] Unknown chat_type: {chat_type}")
        return

    tname = target_name or target_id_str
    message_data = [_serialize_message(seg) for seg in message_content]

    _store_entry(
        chat_type=chat_type,
        target_id=target_id_str,
        target_name=tname,
        user_id=sid,
        nickname=nick,
        message_data=message_data,
        msg_id=msg_id,
    )


# -------------------------------------------------------------------
# 内部通用存储入口
# -------------------------------------------------------------------

def _store_entry(
    *,
    chat_type: str,
    target_id: str,
    target_name: str,
    user_id: str,
    nickname: str,
    message_data: list[dict],
    msg_id: int,
):
    _ensure_dirs()

    if chat_type == "group":
        filepath = GROUP_DIR / f"{target_id}.json"
    else:
        filepath = PRIVATE_DIR / f"{target_id}.json"

    messages = _load_history(filepath)

    entry = {
        "id": msg_id,
        "time": _format_time(),
        "sender": {
            "user_id": user_id,
            "nickname": nickname,
        },
        "target": {
            "type": chat_type,
            "id": target_id,
            "name": target_name,
        },
        "message": message_data,
    }

    messages.append(entry)
    _save_history(filepath, messages)
    # 更新索引
    index = _load_index()
    index[msg_id] = {"chat_type": chat_type, "target_id": target_id}
    _save_index(index)


# -------------------------------------------------------------------
# 接口 4
# -------------------------------------------------------------------

def get_private_messages(user_id: int | str, count: Optional[int] = None) -> str:
    filepath = PRIVATE_DIR / f"{user_id}.json"
    messages = _load_history(filepath)

    if count is not None:
        messages = messages[-count:]

    result_lines: list[str] = []
    for entry in messages:
        time_str = entry["time"]
        nickname = entry["sender"]["nickname"]
        uid = entry["sender"]["user_id"]
        msg_id = entry["id"]
        segments = _reconstruct_segments(entry["message"])
        content = qmsg.parse_msg_to_str(segments)
        result_lines.append(
            f"Time:{time_str} Sender:{nickname}({uid}) Content:{content} msgId:{msg_id}"
        )

    return "\n".join(result_lines)


def get_group_messages(group_id: int | str, count: Optional[int] = None) -> str:
    filepath = GROUP_DIR / f"{group_id}.json"
    messages = _load_history(filepath)

    if count is not None:
        messages = messages[-count:]

    result_lines: list[str] = []
    for entry in messages:
        time_str = entry["time"]
        nickname = entry["sender"]["nickname"]
        uid = entry["sender"]["user_id"]
        group_name = entry["target"]["name"]
        gid = entry["target"]["id"]
        segments = _reconstruct_segments(entry["message"])
        content = qmsg.parse_msg_to_str(segments)
        msg_id = entry["id"]
        result_lines.append(
            f"Time:{time_str} Group:{group_name}({gid}) Sender:{nickname}({uid}) Content:{content} msgId:{msg_id}"
        )

    return "\n".join(result_lines)


# -------------------------------------------------------------------
# 接口 5：获取原始消息对象
# -------------------------------------------------------------------

def get_private_message_objects(user_id: int | str, count: Optional[int] = None) -> list[tuple]:
    """返回重建后的 napcat 消息段元组列表"""
    filepath = PRIVATE_DIR / f"{user_id}.json"
    messages = _load_history(filepath)
    if count is not None:
        messages = messages[-count:]
    return [_reconstruct_segments(entry["message"]) for entry in messages]


def get_group_message_objects(group_id: int | str, count: Optional[int] = None) -> list[tuple]:
    """返回重建后的 napcat 消息段元组列表"""
    filepath = GROUP_DIR / f"{group_id}.json"
    messages = _load_history(filepath)
    if count is not None:
        messages = messages[-count:]
    return [_reconstruct_segments(entry["message"]) for entry in messages]


# -------------------------------------------------------------------
# 备用工具接口
# -------------------------------------------------------------------

def get_message_by_id(msg_id: int) -> Optional[str]:
    """根据消息 id 返回格式化字符串，若未找到返回 None"""
    filepath = _get_filepath_by_msg_id(msg_id)
    if not filepath:
        return None
    messages = _load_history(filepath)
    for entry in messages:
        if entry.get("id") == msg_id:
            segments = _reconstruct_segments(entry["message"])
            content = qmsg.parse_msg_to_str(segments)
            time_str = entry["time"]
            nickname = entry["sender"]["nickname"]
            uid = entry["sender"]["user_id"]
            return f"Time:{time_str} Sender:{nickname}({uid}) Content:{content}"
    # 数据已不在文件中，清理索引
    index = _load_index()
    index.pop(msg_id, None)
    _save_index(index)
    return None


def get_message_object_by_id(msg_id: int) -> Optional[tuple]:
    """根据消息 id 返回重建的 napcat 消息段元组，若未找到返回 None"""
    filepath = _get_filepath_by_msg_id(msg_id)
    if not filepath:
        return None
    messages = _load_history(filepath)
    for entry in messages:
        if entry.get("id") == msg_id:
            return _reconstruct_segments(entry["message"])
    # 数据已不在文件中，清理索引
    index = _load_index()
    index.pop(msg_id, None)
    _save_index(index)
    return None


def delete_message_by_id(msg_id: int) -> bool:
    """根据消息 id 删除指定条目，返回是否成功删除"""
    filepath = _get_filepath_by_msg_id(msg_id)
    if not filepath:
        return False
    messages = _load_history(filepath)
    new_messages = [entry for entry in messages if entry.get("id") != msg_id]
    if len(new_messages) == len(messages):
        return False
    _save_history(filepath, new_messages)
    # 清理索引
    index = _load_index()
    index.pop(msg_id, None)
    _save_index(index)
    return True


def update_message_content_by_id(
    msg_id: int,
    new_content: list[np.Message | np.UnknownMessageSegment],
) -> bool:
    """根据消息 id 更新消息内容（同时更新时间为当前），返回是否成功找到并更新"""
    filepath = _get_filepath_by_msg_id(msg_id)
    if not filepath:
        return False

    messages = _load_history(filepath)
    found = False
    for entry in messages:
        if entry.get("id") == msg_id:
            entry["message"] = [_serialize_message(seg) for seg in new_content]
            entry["time"] = _format_time()
            found = True
            break
    if not found:
        return False
    _save_history(filepath, messages)
    return True


def clear_history(chat_type: str, target_id: int | str):
    if chat_type == "group":
        filepath = GROUP_DIR / f"{target_id}.json"
    else:
        filepath = PRIVATE_DIR / f"{target_id}.json"

    if filepath.exists():
        filepath.unlink()
