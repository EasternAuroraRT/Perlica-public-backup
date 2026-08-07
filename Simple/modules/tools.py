from typing import * # pyright: ignore[reportWildcardImportFromLibrary]
from datetime import datetime
import json
from pathlib import Path
import random
import asyncio
import uuid
import threading
import requests
from openai.types.chat import * # pyright: ignore[reportWildcardImportFromLibrary]
from ddgs import DDGS

import config
from modules.logger import log
from napcat import * # pyright: ignore[reportWildcardImportFromLibrary]
import modules.safe_executor as se
import modules.file_manager as file_manager
import modules.env as env # global status and variant
from modules.chatwindow import ChatWindow, chat_type_str_cn, chat_type_str
import modules.weather as weather
import modules.history as history
import modules.timer as timer
import modules.alarm as alarm
from modules.image_processor import *
from modules.infolib import search_knowledge_base

T = TypeVar("T")
def get_typed_arg(args: dict[str, Any], name: str, t: type[T] | tuple[type[T], ...], default: T | None = None) -> T:
    value = args.get(name, default)
    if not isinstance(value, t):
        raise TypeError(f"参数 `{name}` 类型应为 `{getattr(t, '__name__', t)}`，收到 `{value!r}`")
    return value

# region Async Task Management
_task_store: dict[str, dict] = {}
_task_lock = threading.Lock()

def _tool_run_async(func: Callable[[dict], list], args: dict) -> list[ChatCompletionContentPartParam]:
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
    if asyncio.run(env.active_chatwindow.send()):
        return [{'type': 'text', 'text': f"已向{chat_type_str_cn[env.active_chatwindow.chat_type]}`{env.active_chatwindow.name}`({env.active_chatwindow.chat_id})发送{str(env.active_chatwindow)}. 当前聊天窗口输入框已清空."}]
    else:
        return [{'type': 'text', 'text': f"向{chat_type_str_cn[env.active_chatwindow.chat_type]}`{env.active_chatwindow.name}`({env.active_chatwindow.chat_id})发送消息失败."}]

# def end_reply(_: dict) -> list[ChatCompletionContentPartParam]:
#     return placeholder(_)
# This tool call will be processed directly inside the chat loop.

def recall_msg(args: dict) -> list[ChatCompletionContentPartParam]:
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
        env.active_chatwindow = env.chatwindows[chat_type].get(target_id)
    else:
        return [{'type': 'text', 'text': f"Target chat is not found with id `{target_id}`."}]
    return [{'type': 'text', 'text': "\n".join([f"History:\n{chat_history}", f"Chatbox:\n{str(env.active_chatwindow)}"])}]

def get_image_by_url(args: dict) -> list[ChatCompletionContentPartParam]:
    url = get_typed_arg(args, 'url', str)
    prompt = get_typed_arg(args, 'prompt', (str, type(None)), None)
    image_b64 = get_image_base64_from_url(url)
    try:
        result = get_image_description_from_base64(image_b64, prompt)
        return [{'type': 'text', 'text': result}]
    except Exception as e:
        log.error(f"[tools->get_image_by_url] {e}")
        return [{'type': 'text', 'text': f"Failed to get image from url: {url}\nError: {e}"}]

def get_image_by_path(args: dict) -> list[ChatCompletionContentPartParam]:
    path = get_typed_arg(args, 'path', str)
    prompt = get_typed_arg(args, 'prompt', (str, type(None)), None)
    image_b64 = get_image_base64_from_path(path)
    try:
        result = get_image_description_from_base64(image_b64, prompt)
        return [{'type': 'text', 'text': result}]
    except Exception as e:
        log.error(f"[tools->get_image_by_path] {e}")
        return [{'type': 'text', 'text': f"Failed to get image from path: {path}\nError: {e}"}]

def get_history(args: dict) -> list[ChatCompletionContentPartParam]:
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

def get_random_value(_: dict) -> list[ChatCompletionContentPartParam]:
    return [{'type': 'text', 'text': str(random.random())}]

def set_timer(args: dict) -> list[ChatCompletionContentPartParam]:
    duration = get_typed_arg(args, 'time', (float, int))
    description = get_typed_arg(args, 'description', str)
    id = timer.set_timer(duration, description)
    return [{'type': 'text', 'text': f'Timer {id} is set.'}]

def set_alarm(args: dict) -> list[ChatCompletionContentPartParam]:
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

def cancel_timer(args: dict) -> list[ChatCompletionContentPartParam]:
    timer_id = get_typed_arg(args, 'index', str)
    return [{'type': 'text', 'text': f"{f'Successfully cancelled timer {timer_id}.' if timer.cancel_timer_by_id(timer_id) else f'Failed to cancel timer {timer_id}. Please check.'}"}]

def list_timer(_: dict) -> list[ChatCompletionContentPartParam]:
    return [{'type': 'text', 'text': str(timer.get_all_timers())}]

def list_alarms(_: dict) -> list[ChatCompletionContentPartParam]:
    return [{'type': 'text', 'text': str(alarm.get_all_alarms())}]

def cancel_alarm(args: dict) -> list[ChatCompletionContentPartParam]:
    alarm_id = get_typed_arg(args, 'index', str)
    alarm.cancel_alarm(alarm_id)
    return [{'type': 'text', 'text': f'Alarm {alarm_id} is cancelled'}]

def search_knowledge(args: dict) -> list[ChatCompletionContentPartParam]:
    query = args.get('query')
    top_k = args.get('top_k', 3)
    if not query:
        return [{'type': 'text', 'text': 'Parameter `query` is empty! Please check.'}]
    if not isinstance(top_k, int):
        return [{'type': 'text', 'text': 'Parameter `top_k` is invalid! Please check.'}]
    return search_knowledge_base(query, top_k)

def write_file(args: dict) -> list[ChatCompletionContentPartParam]:
    path = args.get('path','.')
    mode = args.get('mode', 'a')
    content = args.get('content', '')
    file_manager.write_file(path, mode, content)
    return [{'type': 'text', 'text': f"内容已记录: path = {path}, mode = {mode}"}]

def read_file(args: dict) -> list[ChatCompletionContentPartParam]:
    path = args.get('path','.')
    return [{'type': 'text', 'text': file_manager.read_file(path)}]

def cd(args: dict) -> list[ChatCompletionContentPartParam]:
    path = args.get('path','.')
    return [{'type': 'text', 'text': file_manager.cd(path)}]

def ls(args: dict) -> list[ChatCompletionContentPartParam]:
    path = args.get('path','.')
    return [{'type': 'text', 'text': file_manager.ls(path)}]

def mkdir(args: dict) -> list[ChatCompletionContentPartParam]:
    path = args.get('path','.')
    return [{'type': 'text', 'text': file_manager.mkdir(path)}]

def rm(args: dict) -> list[ChatCompletionContentPartParam]:
    path = args.get('path','.')
    r = args.get('recursive', False)
    f = args.get('force', False)
    return [{'type': 'text', 'text': file_manager.rm(path, r, f)}]

def cp(args: dict) -> list[ChatCompletionContentPartParam]:
    src = args.get('src')
    dst = args.get('dst')
    if not src or not dst:
        return [{'type': 'text', 'text': "cp: missing arguments"}]
    r = args.get('recursive', False)
    return [{'type': 'text', 'text': file_manager.cp(src, dst, r)}]

def mv(args: dict) -> list[ChatCompletionContentPartParam]:
    src = args.get('src')
    dst = args.get('dst')
    if not src or not dst:
        return [{'type': 'text', 'text': "mv: missing arguments"}]
    return [{'type': 'text', 'text': file_manager.mv(src, dst)}]

def execute_pystring(args: dict) -> list[ChatCompletionContentPartParam]:
    return _tool_run_async(lambda a: [{'type': 'text', 'text': se.execute_code(a.get('code',''))}], args)

def execute_pyfile(args: dict) -> list[ChatCompletionContentPartParam]:
    return _tool_run_async(lambda a: [{'type': 'text', 'text': se.execute_file(a.get('path',''))}], args)

def get_current_weather(args: dict) -> list[ChatCompletionContentPartParam]:
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
    def _web_search(params: dict):
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
    # return placeholder(args)
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
    return [{"type": "text", "text": f'No disturbing mode is ON and will be set to OFF automatically in {minutes} minute(s).'}]

def set_no_disturb_off(_: dict) -> list[ChatCompletionContentPartParam]:
    env.no_disturb_mode = False
    return [{"type": "text", "text": 'No disturbing mode is set to OFF.'}]

def download_file(args: dict) -> list[ChatCompletionContentPartParam]:
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

def read_qzone(args: dict) -> list[ChatCompletionContentPartParam]:
    return placeholder(args)

#endregion

#region Inner Logic
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

for t in tools_json:
    func_name = t.get("function", {}).get("name")
    if func_name:
        tool_list[func_name] = globals().get(func_name, placeholder)
#endreigion