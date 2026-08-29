from typing import * # pyright: ignore[reportWildcardImportFromLibrary]
from datetime import datetime, date
from pathlib import Path
import re
import threading
import uuid

from openai.types.chat import * # pyright: ignore[reportWildcardImportFromLibrary]

# ==================== 日记搜索 ====================
diary_search_sessions: dict[str, dict] = {}
diary_search_lock = threading.Lock()

def parse_diary_date(s: str, default: date) -> date:
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return default

def _split_diary_line(line: str) -> tuple[str, str]:
    line = line.strip()
    if line.startswith("["):
        idx = line.find("] ")
        if idx != -1:
            return line[1:idx].strip(), line[idx + 2:].strip()
    return "", line

def format_diary_entry(e: dict) -> str:
    ts = f"{e['date']} {e['time']}".strip()
    return f"[{ts}] {e['content']}"

def collect_diary_entries(start: date, end: date) -> list[dict]:
    diary_dir = Path("./diary")
    entries: list[dict] = []
    if not diary_dir.is_dir():
        return entries
    for file_path in sorted(diary_dir.glob("*.txt")):
        try:
            file_date = datetime.strptime(file_path.stem, "%Y-%m-%d").date()
        except ValueError:
            continue
        if not (start <= file_date <= end):
            continue
        with open(file_path, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                time_str, content = _split_diary_line(line)
                entries.append({"date": file_date.strftime("%Y-%m-%d"), "time": time_str, "content": content})
    entries.sort(key=lambda e: (e["date"], e["time"]))
    return entries

def diary_navigate(session_id: str, step: int) -> list[ChatCompletionContentPartParam]:
    direction = "下一条" if step > 0 else "上一条"
    with diary_search_lock:
        session = diary_search_sessions.get(session_id)
        if session is None:
            return [{"type": "text", "text": f"未找到会话 `{session_id}`。搜索会话可能已失效，请重新调用 `search_diary`（mode='interactive'）。"}]
        entries: list[str] = session["entries"]
        total = len(entries)
        index = session["index"]
        new_index = index + step
        if new_index < 0 or new_index >= total:
            boundary = "已经是第一条" if step < 0 else "已经是最后一条"
            return [{"type": "text", "text": f"{boundary}，无法查看{direction}。当前第 {index + 1}/{total} 条。"}]
        session["index"] = new_index
        current = entries[new_index]
    return [{"type": "text", "text": f"第 {new_index + 1}/{total} 条：\n{current}"}]
