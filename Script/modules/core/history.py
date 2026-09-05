from __future__ import annotations
import ast
import json
import threading
from pathlib import Path
from datetime import datetime
from typing import Any, Iterable, Optional, cast
import asyncio

import napcat as np
import modules.core.qmessage as qmsg
import modules.core.env as env
from modules.core.logger import log

HISTORY_DIR = Path(__file__).parent.parent.parent/'history'
GROUP_DIR = HISTORY_DIR / "group"
PRIVATE_DIR = HISTORY_DIR / "private"

APPEND_COMPACT_THRESHOLD = 256

def _ensure_dirs():
    GROUP_DIR.mkdir(parents=True, exist_ok=True)
    PRIVATE_DIR.mkdir(parents=True, exist_ok=True)


INDEX_PATH = HISTORY_DIR / "msg_index.json"
INDEX_LOG_PATH = HISTORY_DIR / "msg_index.jsonl"
_index_cache: dict[int, dict[str, str]] = {}
_index_loaded: bool = False
_index_log_count: int = 0
_history_cache: dict[Path, list[dict]] = {}
_log_counts: dict[Path, int] = {}
_store_lock = threading.RLock()


def _log_path(filepath: Path) -> Path:
    """聊天记录的增量日志路径（与主文件同目录，后缀 .jsonl）"""
    return filepath.with_suffix(filepath.suffix + "l")


def _format_time() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _read_appended(logpath: Path) -> list[dict]:
    """读取 append-only 日志（每行一个 JSON 对象）"""
    if not logpath.exists():
        return []
    try:
        lines = logpath.read_text(encoding='utf-8').splitlines()
    except OSError as e:
        log.error(f"[history->_read_appended] {e}")
        return []
    result: list[dict] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            result.append(data)
    return result


# -------------------------------------------------------------------
# 索引：基文件 + 追加日志（新增 O(1)，达到阈值后压缩回基文件）
# -------------------------------------------------------------------

def _load_index() -> dict[int, dict[str, str]]:
    """加载索引，返回 dict[msg_id, {chat_type, target_id}]（内存缓存，仅首次读盘）"""
    global _index_loaded, _index_log_count
    if not _index_loaded:
        with _store_lock:
            if _index_loaded:
                return _index_cache
            if INDEX_PATH.exists():
                try:
                    data = json.loads(INDEX_PATH.read_text(encoding='utf-8'))
                    _index_cache.update({int(k): v for k, v in data.items()})
                except (json.JSONDecodeError, OSError):
                    _index_cache.clear()
            entries = _read_appended(INDEX_LOG_PATH)
            _index_log_count = len(entries)
            for entry in entries:
                _apply_index_entry(entry)
            _index_loaded = True
    return _index_cache


def _apply_index_entry(entry: dict) -> None:
    msg_id = entry.get("msg_id")
    if msg_id is None:
        return
    msg_id = int(msg_id)
    if entry.get("deleted"):
        _index_cache.pop(msg_id, None)
    else:
        chat_type = entry.get("chat_type")
        target_id = entry.get("target_id")
        if chat_type is not None and target_id is not None:
            _index_cache[msg_id] = {"chat_type": chat_type, "target_id": str(target_id)}


def _append_index(msg_id: int, chat_type: str, target_id: str) -> None:
    global _index_log_count
    with _store_lock:
        _load_index()
        _ensure_dirs()
        _index_cache[msg_id] = {"chat_type": chat_type, "target_id": target_id}
        with open(INDEX_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(
                {"msg_id": msg_id, "chat_type": chat_type, "target_id": target_id},
                ensure_ascii=False,
            ) + "\n")
            f.flush()
        _index_log_count += 1
        if _index_log_count >= APPEND_COMPACT_THRESHOLD:
            _compact_index()


def _pop_index(msg_id: int) -> None:
    global _index_log_count
    with _store_lock:
        _load_index()
        if msg_id not in _index_cache:
            return
        _ensure_dirs()
        _index_cache.pop(msg_id, None)
        with open(INDEX_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps({"msg_id": msg_id, "deleted": True}, ensure_ascii=False) + "\n")
            f.flush()
        _index_log_count += 1
        if _index_log_count >= APPEND_COMPACT_THRESHOLD:
            _compact_index()


def _compact_index() -> None:
    global _index_log_count
    _load_index()
    _ensure_dirs()
    INDEX_PATH.write_text(
        json.dumps({str(k): v for k, v in _index_cache.items()}, ensure_ascii=False, indent=2),
        encoding='utf-8',
    )
    if INDEX_LOG_PATH.exists():
        INDEX_LOG_PATH.unlink()
    _index_log_count = 0


# -------------------------------------------------------------------
# 聊天记录：基文件（旧版完整数组，兼容）+ 追加日志
# -------------------------------------------------------------------

def _load_history(filepath: Path) -> list[dict]:
    cached = _history_cache.get(filepath)
    if cached is not None:
        return cached
    with _store_lock:
        if filepath in _history_cache:
            return _history_cache[filepath]
        base: list[dict] = []
        if filepath.exists():
            try:
                data = json.loads(filepath.read_text(encoding='utf-8'))
                base = data if isinstance(data, list) else []
            except (json.JSONDecodeError, OSError) as e:
                log.error(f"[history->_load_history] {e}")
                base = []
        logpath = _log_path(filepath)
        appended = _read_appended(logpath)
        _log_counts[logpath] = len(appended)
        result = base + appended
        _history_cache[filepath] = result
        return result


def _append_history(filepath: Path, entries: list[dict]) -> None:
    """增量追加：仅把新条目写入 .jsonl 日志并更新内存缓存"""
    with _store_lock:
        _ensure_dirs()
        messages = _load_history(filepath)
        logpath = _log_path(filepath)
        with open(logpath, "a", encoding="utf-8") as f:
            for entry in entries:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            f.flush()
        messages.extend(entries)
        _log_counts[logpath] = _log_counts.get(logpath, 0) + len(entries)
        if _log_counts.get(logpath, 0) >= APPEND_COMPACT_THRESHOLD:
            _compact_history(filepath)


def _compact_history(filepath: Path) -> None:
    """把内存中完整列表写回基文件，清空日志（仅在日志达到阈值时执行）"""
    messages = _history_cache.get(filepath)
    if messages is None:
        return
    _ensure_dirs()
    filepath.write_text(json.dumps(messages, ensure_ascii=False, indent=2), encoding='utf-8')
    logpath = _log_path(filepath)
    if logpath.exists():
        logpath.unlink()
    _log_counts[logpath] = 0


def _rewrite_history(filepath: Path, messages: list[dict]) -> None:
    """完整重写（删除/修改等低频操作使用），同时清空日志并更新缓存"""
    with _store_lock:
        _ensure_dirs()
        filepath.write_text(json.dumps(messages, ensure_ascii=False, indent=2), encoding='utf-8')
        logpath = _log_path(filepath)
        if logpath.exists():
            logpath.unlink()
        _history_cache[filepath] = messages
        _log_counts[logpath] = 0


def _get_filepath_by_msg_id(msg_id: int) -> Optional[Path]:
    """通过索引快速定位消息所在文件，找不到返回 None"""
    entry = _load_index().get(msg_id)
    if not entry:
        return None
    chat_type = entry["chat_type"]
    target_id = entry["target_id"]
    if chat_type == "group":
        return GROUP_DIR / f"{target_id}.json"
    else:
        return PRIVATE_DIR / f"{target_id}.json"


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
# 重建：type+data 直接用构造函数；raw 用白名单类安全重建
# -------------------------------------------------------------------

def _reconstruct_from_raw(raw: str) -> Optional[np.MessageSegment]:
    """将历史 raw 串（如 Text(text='hi')）重建为消息段，仅允许白名单类与字面量参数"""
    try:
        node = ast.parse(raw, mode="eval").body
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            return None
        cls = getattr(np, node.func.id, None)
        if not isinstance(cls, type) or not issubclass(cls, np.MessageSegment):
            return None
        args = [ast.literal_eval(a) for a in node.args]
        kwargs = {kw.arg: ast.literal_eval(kw.value) for kw in node.keywords if kw.arg is not None}
        obj = cls(*args, **kwargs)
        return obj if isinstance(obj, np.MessageSegment) else None
    except Exception as e:
        log.error(f"[history->_reconstruct_from_raw] {e}")
        return None


def _reconstruct_segments(msg_list: list[dict]) -> tuple:
    segments = []
    for seg in msg_list:
        msg_type = seg.get("type")
        msg_data = seg.get("data")
        if msg_type is not None and msg_data is not None:
            cls = getattr(np, msg_type, None)
            if isinstance(cls, type) and issubclass(cls, np.MessageSegment):
                try:
                    obj = cls(**msg_data) if isinstance(msg_data, dict) else cast(Any, cls)(msg_data)
                    segments.append(obj)
                    continue
                except Exception as e:
                    log.error(f"[history->_reconstruct_segments->msg_type] {e}")
        raw = seg.get("raw", "")
        if raw:
            obj = _reconstruct_from_raw(raw)
            if obj is not None:
                segments.append(obj)
                continue
        segments.append(np.Text(text=raw))
    return tuple(segments)


# -------------------------------------------------------------------
# 接口 1 / 2
# -------------------------------------------------------------------

def store_group_message(event: np.GroupMessageEvent):
    store_message(
        "group",
        str(event.group_id),
        event.message,
        sender_id=str(event.sender.user_id),
        sender_nickname=event.sender.nickname,
        target_name=str(getattr(event, "group_name", event.group_id)),
        msg_id=event.message_id,
    )


def store_private_message(event: np.PrivateMessageEvent):
    store_message(
        "private",
        str(event.sender.user_id),
        event.message,
        sender_id=str(event.sender.user_id),
        sender_nickname=event.sender.nickname,
        target_name=event.sender.nickname,
        msg_id=event.message_id,
    )


# -------------------------------------------------------------------
# 接口 3
# -------------------------------------------------------------------

def store_message(
    chat_type: str,
    target_id: int | str,
    message_content: Iterable[np.Message | np.UnknownMessageSegment],
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

    _append_history(filepath, [entry])
    if msg_id:
        _append_index(int(msg_id), chat_type, target_id)


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

# def get_private_message_objects(user_id: int | str, count: Optional[int] = None) -> list[tuple]:
#     """返回重建后的 napcat 消息段元组列表"""
#     filepath = PRIVATE_DIR / f"{user_id}.json"
#     messages = _load_history(filepath)
#     if count is not None:
#         messages = messages[-count:]
#     return [_reconstruct_segments(entry["message"]) for entry in messages]


# def get_group_message_objects(group_id: int | str, count: Optional[int] = None) -> list[tuple]:
#     """返回重建后的 napcat 消息段元组列表"""
#     filepath = GROUP_DIR / f"{group_id}.json"
#     messages = _load_history(filepath)
#     if count is not None:
#         messages = messages[-count:]
#     return [_reconstruct_segments(entry["message"]) for entry in messages]


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
    _pop_index(msg_id)
    return None


# def get_message_object_by_id(msg_id: int) -> Optional[tuple]:
#     """根据消息 id 返回重建的 napcat 消息段元组，若未找到返回 None"""
#     filepath = _get_filepath_by_msg_id(msg_id)
#     if not filepath:
#         return None
#     messages = _load_history(filepath)
#     for entry in messages:
#         if entry.get("id") == msg_id:
#             return _reconstruct_segments(entry["message"])
#     _pop_index(msg_id)
#     return None


def delete_message_by_id(msg_id: int) -> bool:
    """根据消息 id 删除指定条目，返回是否成功删除"""
    filepath = _get_filepath_by_msg_id(msg_id)
    if not filepath:
        return False
    messages = _load_history(filepath)
    new_messages = [entry for entry in messages if entry.get("id") != msg_id]
    if len(new_messages) == len(messages):
        return False
    _rewrite_history(filepath, new_messages)
    _pop_index(msg_id)
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
    _rewrite_history(filepath, messages)
    return True


def clear_history(chat_type: str, target_id: int | str):
    filepath = GROUP_DIR / f"{target_id}.json" if chat_type == "group" else PRIVATE_DIR / f"{target_id}.json"

    with _store_lock:
        if filepath.exists():
            filepath.unlink()
        logpath = _log_path(filepath)
        if logpath.exists():
            logpath.unlink()
        _history_cache.pop(filepath, None)
        _log_counts.pop(logpath, None)
