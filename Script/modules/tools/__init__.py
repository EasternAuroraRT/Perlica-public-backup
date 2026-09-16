from typing import * # pyright: ignore[reportWildcardImportFromLibrary]
from datetime import datetime
import json
import re
from pathlib import Path
import random
import asyncio
import uuid
import threading
import requests
from openai.types.chat import * # pyright: ignore[reportWildcardImportFromLibrary]
from ddgs import DDGS
import shutil
import os

from config import config
from napcat import * # pyright: ignore[reportWildcardImportFromLibrary]
from modules.core.logger import log
import modules.core.env as env # global status and variant
from modules.core.chatwindow import ChatWindow, chat_type_str_cn, chat_type_str
from .impl import bio_text
from modules.simulation import biosim

# region Reflection
T = TypeVar("T")
def get_typed_arg(args: dict[str, Any], name: str, t: type[T] | tuple[type[T], ...], default: T | None = None) -> T:
    value = args.get(name, default)
    if not isinstance(value, t):
        raise TypeError(f"参数 `{name}` 类型应为 `{getattr(t, '__name__', t)}`，收到 `{value!r}`")
    return value

tools_json = json.loads(Path(__file__).with_name("tools.json").read_text(encoding='utf8'))["tools"]
tool_list: dict[str, Callable[[dict], list[ChatCompletionContentPartParam]]] = {}

def call_tool(tool_name: str, json_arg: str) -> list[ChatCompletionContentPartParam]:
    try:
        args = json.loads(json_arg)
    except json.JSONDecodeError:
        log.error(f"参数解析错误: `{json_arg}` 不是合法 JSON")
        return [{'type': 'text', 'text': f"Unable to call tool \"{tool_name}\" with given arguments, but this may not be your fault."}]
    func = tool_list.get(tool_name, placeholder)
    result: list[ChatCompletionContentPartParam]
    try:
        result = func(args)
    except Exception as e:
        result = [{'type': 'text', 'text': str(e)}]
    return result
# endregion

# region Async Task Management
_task_store: dict[str, dict] = {}
_task_lock = threading.Lock()

def _tool_run_async(func: Callable[[dict], list[ChatCompletionContentPartParam]], args: dict) -> list[ChatCompletionContentPartParam]:
    """Start a long-running tool in a background thread, returning an immediate result with a task_id."""
    task_id = str(uuid.uuid4())
    with _task_lock:
        _task_store[task_id] = {
            'status': 'running',
            'result': None,
        }
    def target():
        try:
            result = func(args)
            with _task_lock:
                if task_id in _task_store:
                    _task_store[task_id]['status'] = 'done'
                    _task_store[task_id]['result'] = result
        except Exception as e:
            log.error(f"[tools->_run_async->target] {e}")
            with _task_lock:
                if task_id in _task_store:
                    _task_store[task_id]['status'] = 'error'
                    _task_store[task_id]['result'] = {'type': 'text', 'text': f"Async tool execution error: {e}"}
    t = threading.Thread(target=target, daemon=True)
    t.start()
    return [{
        'type': 'text',
        'text': f"长耗时任务已启动，任务 id 为 `{task_id}`。请稍后使用 `get_tool_result` 传入此 id 查询状态和结果。等待结果的过程中你可以与其他人互动。"
    }]
# endregion

# region Tool Defs
def placeholder(_: dict) -> list[ChatCompletionContentPartParam]:
    return [{'type': 'text', 'text': "当前工具正在开发中, 暂时无法正常工作"}]

def get_tool_result(args: dict) ->list[ChatCompletionContentPartParam]:
    task_id = get_typed_arg(args, 'task_id', str)
    with _task_lock:
        entry = _task_store.get(task_id)
        if entry is None:
            return [{'type': 'text', 'text': f"未找到 id 为 `{task_id}` 的任务。"}]
        if entry['status'] == 'running':
            return [{'type': 'text', 'text': f"任务 `{task_id}` 仍在运行中，请稍后再试。为避免频繁调用，你可以使用计时器设置等待时间，并结束调用。"}]
        else:
            try:
                assert isinstance(entry['result'], list)
                result = entry['result'].copy()
                del _task_store[task_id]
                return result
            except Exception as e:
                return [{'type': 'text', 'text': f"工具结果获取异常: {e}"}]

def type_text(args: dict) -> list[ChatCompletionContentPartParam]:
    if not env.active_chatwindow:
        return [{'type': 'text', 'text': "当前未选择聊天窗口，请选择后重试。"}]
    text = get_typed_arg(args, 'text', str)
    env.active_chatwindow.type_txt(text)
    return [{'type': 'text', 'text': "已写入内容至当前聊天框, 需要时可调用`check_chatbox`检查聊天框全部内容."}]

def type_del(args: dict) -> list[ChatCompletionContentPartParam]:
    num = get_typed_arg(args, "delnum", int)
    env.active_chatwindow.type_del(num)
    return [{'type': 'text', 'text': "已删除指定数量内容. 注意: 部分内容可能被视作整体删除; 需要时可调用`check_chatbox`检查聊天框全部内容."}]

def send_msg(_: dict) -> list[ChatCompletionContentPartParam]:
    if not env.active_chatwindow.content:
        return [{'type': 'text', 'text': "你还没有输入任何内容！"}]
    asyncio.run(env.active_chatwindow.send())
    return [{'type': 'text', 'text': f"已向{chat_type_str_cn[env.active_chatwindow.chat_type]}`{env.active_chatwindow.name}`({env.active_chatwindow.chat_id})发送{str(env.active_chatwindow)}. 当前聊天窗口输入框已清空."}]

# def end_action(_: dict) -> list[ChatCompletionContentPartParam]:
#     return placeholder(_)
# This tool call will be processed directly inside the chat loop.

def recall_msg(args: dict) -> list[ChatCompletionContentPartParam]:
    import modules.core.history as history
    msg_id = get_typed_arg(args, 'message_id', (int, str))
    try:
        asyncio.run(env.npclient.delete_msg(message_id=msg_id))
    except Exception as e:
        return [{'type': 'text', 'text': f"Failed to recall message {msg_id}: {str(e)}"}]
    history.delete_message_by_id(int(msg_id))
    return [{'type': 'text', 'text': f"Message {msg_id} is recalled."}]

def check_chatbox(_: dict) -> list[ChatCompletionContentPartParam]:
    return [{'type': 'text', 'text': str(env.active_chatwindow)}]

def find_cursor(_: dict) -> list[ChatCompletionContentPartParam]:
    return [{'type': 'text', 'text': str(env.active_chatwindow.cursor)}]

def switch_chat_window(args: dict) -> list[ChatCompletionContentPartParam]:
    import modules.core.history as history
    type_str = get_typed_arg(args, 'type', str)
    target_id = get_typed_arg(args, 'id', str)
    chat_type: ChatWindow.ChatType
    chat_history: str
    match type_str:
        case 'private':
            chat_type = ChatWindow.ChatType.Private
            chat_history = history.get_private_messages(target_id, 5)
        case 'group':
            chat_type = ChatWindow.ChatType.Group
            chat_history = history.get_group_messages(target_id, 5)
        case _:
            return [{'type': 'text', 'text': f"Unable to parse type `{type_str}`."}]
    if target_id in env.chatwindows[chat_type]:
        env.active_chatwindow = env.chatwindows[chat_type].get(target_id, env.active_chatwindow)
        log.info(f"[tool info] current in chat {env.active_chatwindow.name}")
    else:
        return [{'type': 'text', 'text': f"Target chat is not found with id `{target_id}`."}]
    return [{'type': 'text', 'text': "\n".join([f"History:\n{chat_history}", f"Chatbox:\n{str(env.active_chatwindow)}"])}]

def get_image_by_url(args: dict) -> list[ChatCompletionContentPartParam]:
    from .impl.image_processor import get_image_base64_from_url, get_image_description_from_base64
    url = get_typed_arg(args, 'url', str)
    prompt = get_typed_arg(args, 'prompt', (str, type(None)), None)
    image_b64 = get_image_base64_from_url(url)
    if config.is_multimodal():
        return [{'type': 'image_url', 'image_url': {"url": image_b64}}]
    else:
        try:
            result = get_image_description_from_base64(image_b64, prompt)
            return [{'type': 'text', 'text': result}]
        except Exception as e:
            log.error(f"[tools->get_image_by_url] {e}")
            return [{'type': 'text', 'text': f"Failed to get image from url: {url}\nError: {e}"}]

def get_image_by_path(args: dict) -> list[ChatCompletionContentPartParam]:
    from .impl.image_processor import get_image_base64_from_path, get_image_description_from_base64
    path = get_typed_arg(args, 'path', str)
    prompt = get_typed_arg(args, 'prompt', (str, type(None)), None)
    image_b64 = get_image_base64_from_path(path)
    if config.is_multimodal():
            return [{'type': 'image_url', 'image_url': {"url": image_b64}}]
    else:
        try:
            result = get_image_description_from_base64(image_b64, prompt)
            return [{'type': 'text', 'text': result}]
        except Exception as e:
            log.error(f"[tools->get_image_by_path] {e}")
            return [{'type': 'text', 'text': f"Failed to get image from path: {path}\nError: {e}"}]

def get_history(args: dict) -> list[ChatCompletionContentPartParam]:
    import modules.core.history as history
    count = get_typed_arg(args, 'count', int)
    result: str = ''
    match env.active_chatwindow.chat_type:
        case ChatWindow.ChatType.Private:
            result = history.get_private_messages(env.active_chatwindow.chat_id ,count)
        case ChatWindow.ChatType.Group:
            result = history.get_group_messages(env.active_chatwindow.chat_id, count)
        case _:
            result = f"Failed to get history message! Current chat window is invalid."
    return [{'type': 'text', 'text': result}]

def get_current_time(args: dict) -> list[ChatCompletionContentPartParam]:
    default_format = "%Y-%m-%d %H:%M:%S"
    fmt = args.get("format")
    if not isinstance(fmt, str):
        fmt = default_format
    result: str
    try:
        result = datetime.now().strftime(fmt)
    except (ValueError, TypeError) as e:
        log.error(f"[tools->get_current_time] {e}")
        result = f"Invalid format: '{fmt}', using default.\n" + datetime.now().strftime(default_format)
    return [{'type': 'text', 'text': result}]

def get_date_info(args: dict) -> list[ChatCompletionContentPartParam]:
    from .impl.lunar import get_lunar_info
    date_str = get_typed_arg(args, 'date', str, '')
    if date_str:
        try:
            d = datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            return [{'type': 'text', 'text': f"日期格式错误: `{date_str}`，请使用 `YYYY-MM-DD` 格式。"}]
    else:
        d = datetime.now().date()
    info = get_lunar_info(d)
    text = (
        f"公历：{info['solar']} {info['weekday']}\n"
        f"农历：{info['lunar_text']}（{info['zodiac']}年）"
    )
    return [{'type': 'text', 'text': text}]

def append_diary(args: dict) -> list[ChatCompletionContentPartParam]:
    content = get_typed_arg(args, 'content', str)
    diary_dir = Path("./diary")
    diary_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    file_path = diary_dir / f"{today}.txt"
    timestamp = datetime.now().strftime("%H:%M:%S")
    with open(file_path, 'a', encoding='utf-8') as f:
        f.write(f"[{timestamp}] {content}\n")
    return [{'type': 'text', 'text': f"Operation done."}]

def read_diary(args: dict) -> list[ChatCompletionContentPartParam]:
    diary_dir = Path("./diary")
    date = get_typed_arg(args, 'date', str)
    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        date = datetime.now().strftime("%Y-%m-%d")
    file_path = diary_dir / f"{date}.txt"
    if not file_path.exists():
        return [{'type': 'text', 'text': ""}]
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()
    return [{'type': 'text', 'text': content}]

def search_diary(args: dict) -> list[ChatCompletionContentPartParam]:
    from .impl import diary
    query = get_typed_arg(args, "query", str, "")
    start_date = get_typed_arg(args, "start_date", str, "")
    end_date = get_typed_arg(args, "end_date", str, "")
    mode = get_typed_arg(args, "mode", str, "command")
    max_entries = get_typed_arg(args, "max_entries", int, 20)

    if mode not in ("command", "interactive"):
        return [{"type": "text", "text": f"无效模式 `{mode}`，应为 `command` 或 `interactive`。"}]

    start = diary.parse_diary_date(start_date, diary.date.min)
    end = diary.parse_diary_date(end_date, diary.date.max)
    if start > end:
        return [{"type": "text", "text": f"起始日期 `{start_date}` 晚于结束日期 `{end_date}`，请检查。"}]

    pattern: re.Pattern[str] | None = None
    literal_fallback = False
    if query:
        try:
            pattern = re.compile(query)
        except re.error:
            pattern = re.compile(re.escape(query))
            literal_fallback = True

    matches: list[dict] = []
    for entry in diary.collect_diary_entries(start, end):
        if pattern is None or pattern.search(entry["content"]):
            matches.append(entry)

    total = len(matches)
    if total == 0:
        msg = "未找到匹配的日记条目。"
        if literal_fallback:
            msg += "（提示：正则表达式无效，已按普通字符串匹配。）"
        return [{"type": "text", "text": msg}]

    if mode == "command":
        shown = matches[:max_entries] if max_entries > 0 else matches
        lines = [f"共匹配 {total} 条日记："]
        if literal_fallback:
            lines.insert(0, "（提示：正则表达式无效，已按普通字符串匹配。）")
        for i, entry in enumerate(shown, 1):
            lines.append(f"{i}. {diary.format_diary_entry(entry)}")
        if len(shown) < total:
            lines.append(f"（已返回前 {len(shown)} 条。可通过 `max_entries` 获取更多，或使用 `mode='interactive'` 逐条浏览。）")
        return [{"type": "text", "text": "\n".join(lines)}]

    session_id = str(uuid.uuid4())
    formatted = [diary.format_diary_entry(e) for e in matches]
    with diary.diary_search_lock:
        diary.diary_search_sessions[session_id] = {"entries": formatted, "index": 0}

    header = f"已找到 {total} 条匹配日记。"
    if literal_fallback:
        header += "（提示：正则表达式无效，已按普通字符串匹配。）"
    nav = f"当前第 1/{total} 条。"
    if total > 1:
        nav += f" 调用 `next_diary` 查看下一条，`prev_diary` 查看上一条，session_id 为 `{session_id}`。"
    else:
        nav += " 仅此一条。"
    return [{"type": "text", "text": f"{header}\n\n{formatted[0]}\n\n{nav}"}]

def next_diary(args: dict) -> list[ChatCompletionContentPartParam]:
    from .impl import diary
    session_id = get_typed_arg(args, "session_id", str)
    return diary.diary_navigate(session_id, +1)

def prev_diary(args: dict) -> list[ChatCompletionContentPartParam]:
    from .impl import diary
    session_id = get_typed_arg(args, "session_id", str)
    return diary.diary_navigate(session_id, -1)

def get_random_value(_: dict) -> list[ChatCompletionContentPartParam]:
    return [{'type': 'text', 'text': str(random.random())}]

def set_timer(args: dict) -> list[ChatCompletionContentPartParam]:
    from .impl import timer
    duration = get_typed_arg(args, 'time', (float, int))
    description = get_typed_arg(args, 'description', str)
    id = timer.set_timer(duration, description)
    return [{'type': 'text', 'text': f'Timer {id} is set.'}]

def set_alarm(args: dict) -> list[ChatCompletionContentPartParam]:
    from .impl import alarm
    time_str = get_typed_arg(args, 'time', str)
    loop = get_typed_arg(args, 'loop', str, 'once')
    if loop not in ('once', 'daily', 'weekly'):
        raise ValueError(f"Invalid loop `{loop}`, expected once/daily/weekly")
    about = get_typed_arg(args, 'description', str, '')
    try:
        id = alarm.set_alarm(time_str, loop, about)
        return [{'type': 'text', 'text': f"Alarm {id} set successfully with {f'description `{about}`' if about else 'no description'}."}]
    except Exception as e:
        return [{'type': 'text', 'text': str(e)}]

def list_timer(_: dict) -> list[ChatCompletionContentPartParam]:
    from .impl import timer
    return [{'type': 'text', 'text': str(timer.get_all_timers())}]

def cancel_timer(args: dict) -> list[ChatCompletionContentPartParam]:
    from .impl import timer
    timer_id = get_typed_arg(args, 'index', str)
    return [{'type': 'text', 'text': f"{f'Successfully cancelled timer {timer_id}.' if timer.cancel_timer_by_id(timer_id) else f'Failed to cancel timer {timer_id}. Please check.'}"}]

def list_alarms(_: dict) -> list[ChatCompletionContentPartParam]:
    from .impl import alarm
    return [{'type': 'text', 'text': str(alarm.get_all_alarms())}]

def cancel_alarm(args: dict) -> list[ChatCompletionContentPartParam]:
    from .impl import alarm
    alarm_id = get_typed_arg(args, 'index', str)
    alarm.cancel_alarm(alarm_id)
    return [{'type': 'text', 'text': f'Alarm {alarm_id} is cancelled'}]

def set_schedule(args: dict) -> list[ChatCompletionContentPartParam]:
    from .impl import schedule
    time_str = get_typed_arg(args, 'time', str)
    about = get_typed_arg(args, 'description', str, '')
    try:
        schedule_id = schedule.set_schedule(time_str, about)
        return [{'type': 'text', 'text': f"Schedule {schedule_id} set successfully with {f'description `{about}`' if about else 'no description'}."}]
    except Exception as e:
        return [{'type': 'text', 'text': str(e)}]

def list_schedules(_: dict) -> list[ChatCompletionContentPartParam]:
    from .impl import schedule
    return [{'type': 'text', 'text': str(schedule.get_all_schedules())}]

def cancel_schedule(args: dict) -> list[ChatCompletionContentPartParam]:
    from .impl import schedule
    schedule_id = get_typed_arg(args, 'index', str)
    schedule.cancel_schedule(schedule_id)
    return [{'type': 'text', 'text': f'Schedule {schedule_id} is cancelled'}]

def search_knowledge(args: dict) -> list[ChatCompletionContentPartParam]:
    from modules.knowledge.infolib import search_knowledge_base
    def _search_knowledge(args: dict) -> list[ChatCompletionContentPartParam]:
        query = args.get('query')
        top_k = args.get('top_k', 3)
        if not query:
            return [{'type': 'text', 'text': 'Parameter `query` is empty! Please check.'}]
        if not isinstance(top_k, int):
            return [{'type': 'text', 'text': 'Parameter `top_k` is invalid! Please check.'}]
        return search_knowledge_base(query, top_k)
    return _tool_run_async(_search_knowledge, args)

def write_file(args: dict) -> list[ChatCompletionContentPartParam]:
    from .impl import file_manager
    path = args.get('path','.')
    mode = args.get('mode', 'a')
    content = args.get('content', '')
    file_manager.write_file(path, mode, content)
    return [{'type': 'text', 'text': f"内容已记录: path = {path}, mode = {mode}"}]

def read_file(args: dict) -> list[ChatCompletionContentPartParam]:
    from .impl import file_manager
    path = args.get('path','.')
    return [{'type': 'text', 'text': file_manager.read_file(path)}]

def cd(args: dict) -> list[ChatCompletionContentPartParam]:
    from .impl import file_manager
    path = args.get('path','.')
    return [{'type': 'text', 'text': file_manager.cd(path)}]

def ls(args: dict) -> list[ChatCompletionContentPartParam]:
    from .impl import file_manager
    path = args.get('path','.')
    return [{'type': 'text', 'text': file_manager.ls(path)}]

def mkdir(args: dict) -> list[ChatCompletionContentPartParam]:
    from .impl import file_manager
    path = args.get('path','.')
    return [{'type': 'text', 'text': file_manager.mkdir(path)}]

def rm(args: dict) -> list[ChatCompletionContentPartParam]:
    from .impl import file_manager
    path = args.get('path','.')
    r = args.get('recursive', False)
    f = args.get('force', False)
    return [{'type': 'text', 'text': file_manager.rm(path, r, f)}]

def cp(args: dict) -> list[ChatCompletionContentPartParam]:
    from .impl import file_manager
    src = args.get('src')
    dst = args.get('dst')
    if not src or not dst:
        return [{'type': 'text', 'text': "cp: missing arguments"}]
    r = args.get('recursive', False)
    return [{'type': 'text', 'text': file_manager.cp(src, dst, r)}]

def mv(args: dict) -> list[ChatCompletionContentPartParam]:
    from .impl import file_manager
    src = args.get('src')
    dst = args.get('dst')
    if not src or not dst:
        return [{'type': 'text', 'text': "mv: missing arguments"}]
    return [{'type': 'text', 'text': file_manager.mv(src, dst)}]

def execute_pystring(args: dict) -> list[ChatCompletionContentPartParam]:
    from .impl import safe_executor as se
    return _tool_run_async(lambda a: [{'type': 'text', 'text': se.execute_code(a.get('code',''))}], args)

def execute_pyfile(args: dict) -> list[ChatCompletionContentPartParam]:
    from .impl import safe_executor as se
    return _tool_run_async(lambda a: [{'type': 'text', 'text': se.execute_file(a.get('path',''))}], args)

def get_current_weather(args: dict) -> list[ChatCompletionContentPartParam]:
    from .impl import weather
    return _tool_run_async(lambda a: [{'type': 'text', 'text': str(weather.get_current_weather(a.get('location','')))}], args)

def send_poke(args: dict) -> list[ChatCompletionContentPartParam]:
    target = get_typed_arg(args, 'target_id', str)
    group = get_typed_arg(args, 'group_id', str, '')
    if group:
        asyncio.run(env.npclient.send_poke(user_id=target, group_id=group))
    else:
        asyncio.run(env.npclient.send_poke(user_id=target))
    result = f'Poked user {target} {f"in group {group} " if group else ''}successfully.'
    return [{'type': 'text', 'text': result}]

def get_user_info(args: dict) -> list[ChatCompletionContentPartParam]:
    user_id = get_typed_arg(args, 'user_id', str)
    return [{'type': 'text', 'text': str(asyncio.run(env.npclient.get_stranger_info(user_id=user_id)))}]

def get_msg_by_id(args: dict) -> list[ChatCompletionContentPartParam]:
    msg_id = get_typed_arg(args, 'id', int)
    return [{'type': 'text', 'text': str(asyncio.run(env.npclient.get_msg(message_id=msg_id)))}]

def web_search(args: dict) -> List[ChatCompletionContentPartParam]:
    def _web_search(params: dict) -> List[ChatCompletionContentPartParam]:
        query = params.get("query")
        if not query:
            return [{"type": "text", "text": "错误：缺少 'query' 参数"}]
        max_results = params.get("max_results", 10)
        try:
            with DDGS() as ddgs:
                results = list(ddgs.text(query, max_results=max_results))
            if not results:
                return [{"type": "text", "text": f"未找到与 '{query}' 相关的结果"}]
            # 将搜索结果拼接成可读文本，每个结果包含标题、正文和链接
            text_parts = []
            for idx, r in enumerate(results, 1):
                title = r.get("title", "无标题")
                body = r.get("body", "无摘要")
                link = r.get("href", "#")
                text_parts.append(f"{idx}. {title}\n   {body}\n   来源: {link}")
            full_text = f"搜索结果（共{len(results)}条）：\n" + "\n\n".join(text_parts)
            return [{"type": "text", "text": full_text}]
        except Exception as e:
            # 任何异常都返回错误信息（不抛出异常）
            return [{"type": "text", "text": f"搜索时发生错误: {str(e)}"}]
    return _tool_run_async(_web_search, args)

def read_web(args: dict) -> list:
    def _read_web(params: dict) -> list[ChatCompletionContentPartParam]:
        url = params.get("url")
        if not url:
            return [{"type": "text", "text": "错误：缺少 'url' 参数"}]
        timeout = params.get("timeout", 30)
        # Jina Reader API 端点：在目标 URL 前加上 https://r.jina.ai/
        reader_url = f"https://r.jina.ai/{url}"
        try:
            response = requests.get(reader_url, timeout=timeout)
            response.raise_for_status()
            # 默认返回 Markdown 格式文本
            content = response.text
            if not content or len(content.strip()) == 0:
                raise Exception(f"{url} 返回的内容为空", response)
            return [{"type": "text", "text": content}]
        except Exception as e:
            return [{"type": "text", "text": str(e)}]
    return _tool_run_async(_read_web, args)

def set_no_disturb_on(args: dict) -> list[ChatCompletionContentPartParam]:
    minutes = get_typed_arg(args, 'time', int, 1440)
    if minutes <= 0:
        raise ValueError("time must be positive")
    env.no_disturb_mode = True
    def action():
        env.no_disturb_mode = False
    disturber = threading.Timer(minutes*60, action)
    disturber.daemon = True
    disturber.start()
    return [{"type": "text", "text": f'No-disturbing mode is ON and will be set to OFF automatically in {minutes} minute(s).'}]
def set_no_disturb_off(_: dict) -> list[ChatCompletionContentPartParam]:
    env.no_disturb_mode = False
    return [{"type": "text", "text": 'No-disturbing mode is set to OFF.'}]

def download_file(args: dict) -> list[ChatCompletionContentPartParam]:
    from .impl import file_manager
    url = get_typed_arg(args, 'url', str)
    save_path = get_typed_arg(args, 'save_path', str, '/')
    file_name = get_typed_arg(args, 'file_name', str)
    def download(_) -> list:
        r = requests.get(url=url, stream=True)
        with open(Path(file_manager.get_root())/save_path/file_name, 'wb') as f:
            for chunk in r.iter_content(chunk_size=4096):
                f.write(chunk)
        result = f'文件已保存至"{save_path}".'
        return [{"type": "text", "text": result}]
    return _tool_run_async(download, {})

def send_file(args: dict) -> list[ChatCompletionContentPartParam]:
    from .impl import file_server
    # return placeholder(args)
    file_path = get_typed_arg(args, 'file_path', str)
    path: Path = Path(file_path)
    file_name = get_typed_arg(args, 'file_name', str, path.name)
    result: str = ''
    try:
        src = Path(file_path).resolve()
        url = file_server.publish(src)
        log.info(f"File url = {url}")
        env.active_chatwindow.add_file(url, file_name)
        asyncio.run(env.active_chatwindow.send())
        result = f'File `{path.name}` uploaded successfully.'
    except Exception as e:
        log.error(f"[{__file__}->send_file] Failed to upload file `{file_path}`.\n{e}")
        result = f'Failed to upload file `{file_path}`.\n{e}'
    return [{"type": "text", "text": result}]


def sys_cmd(args: dict) -> list[ChatCompletionContentPartParam]:
    from .impl import command
    cmd = get_typed_arg(args, 'command', str)
    is_fast_cmd = get_typed_arg(args, 'is_fast', bool, False)
    def run_cmd(args: dict) -> list[ChatCompletionContentPartParam]:
        cmd = args.get("command", "")
        return [{"type": "text", "text": command.run_command(cmd)}]
    if is_fast_cmd:
        return run_cmd({"command": cmd})
    else:
        return _tool_run_async(run_cmd, {"command": cmd})

# ---- 生物状态与动作（modules.simulation.biosim） ----

def get_self_bio_state(_: dict) -> list[ChatCompletionContentPartParam]:
    return [{"type": "text", "text": bio_text.state_text(env.biosim_engine)}]

def goto_sleep(_: dict) -> list[ChatCompletionContentPartParam]:
    effect = biosim.SleepEffect()
    reason = effect.refusal(env.biosim_engine.get_slice())
    if reason:
        return [{"type": "text", "text": reason}]
    env.biosim_engine.add_effect(effect)
    return [{"type": "text", "text": "Sleeping... Call end_action to terminate. You may wake up by yourself or by alarms and emergency."}]

def wake_up(_: dict) -> list[ChatCompletionContentPartParam]:
    env.biosim_engine.add_effect(biosim.WakeEffect())
    return [{"type": "text", "text": "Awake."}]

def eat(args: dict) -> list[ChatCompletionContentPartParam]:
    from .impl import food
    name = get_typed_arg(args, "food", str)
    if not name.strip():
        return [{"type": "text", "text": "No food given."}]
    portion, quality = food.judge(name, get_typed_arg(args, "amount", (int, float), 1.0))
    effect = biosim.EatEffect(portion=portion, quality=quality)
    reason = effect.refusal(env.biosim_engine.get_slice())
    if reason:
        return [{"type": "text", "text": reason}]
    env.biosim_engine.add_effect(effect)
    return [{"type": "text", "text": f"正在吃`{name}`"}]

def exercise(args: dict) -> list[ChatCompletionContentPartParam]:
    from .impl import exertion
    kind = get_typed_arg(args, "kind", str)
    if not kind.strip():
        return [{"type": "text", "text": "No exercise given."}]
    intensity, minutes = exertion.judge(kind, get_typed_arg(args, "minutes", (int, float), 20.0))
    effect = biosim.ExerciseEffect(intensity=intensity, minutes=minutes)
    reason = effect.refusal(env.biosim_engine.get_slice())
    if reason:
        return [{"type": "text", "text": reason}]
    env.biosim_engine.add_effect(effect)
    return [{"type": "text", "text": f"开始{kind}。"}]

def stop_exercise(_: dict) -> list[ChatCompletionContentPartParam]:
    for effect in env.biosim_engine.effects:
        if isinstance(effect, biosim.ExerciseEffect):
            env.biosim_engine.remove_effect(effect)
            break
    return [{"type": "text", "text": "Stopped."}]

# -------------
#endregion

#region Initialize
for t in tools_json:
    func_name = t.get("function", {}).get("name")
    if func_name:
        tool_list[func_name] = globals().get(func_name, placeholder)
#endreigion