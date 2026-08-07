from __future__ import annotations
import asyncio
import json
from typing import * # pyright: ignore[reportWildcardImportFromLibrary]
from pathlib import Path
from openai.types.chat import * # pyright: ignore[reportWildcardImportFromLibrary]

from napcat import * # pyright: ignore[reportWildcardImportFromLibrary]
import modules.env as env
import config
from modules.logger import log
from modules.image_processor import get_image_base64_from_url

_qface_base_path: Path = Path(__file__).parent.parent/"Qface"
_qface_index_path: Path = _qface_base_path/"assets/qq_emoji/_index.json"

class QFaceAsset(TypedDict):
    type: Required[int]
    name: Required[str]
    path: Required[str]

class QFace(TypedDict, total=False):
    emojiId: Required[str]
    describe: Required[str]
    qzoneCode: Required[str]
    qcid: Required[int]
    emojiType: Required[int]
    aniStickerPackId: Required[int]
    aniStickerId: Required[int]
    associateWords: Required[list[str]]
    isHide: Required[bool]
    startTime: Required[str]
    endTime: Required[str]
    animationWidth: Required[int]
    animationHeigh: Required[int]
    assets: Required[list[QFaceAsset]]

def parse_msg_to_str(msg: tuple[Message | UnknownMessageSegment, ...]) -> str:
    result: str  = ''
    for seg in msg:
        match seg:
            case Reply():
                try:
                  if seg.id:
                      result += f"Reply(id='{seg.id}', raw={asyncio.run(env.npclient.get_msg(message_id=int(seg.id)))})"
                except Exception as e:
                    log.warning(f'[qmessage->parse_msg_to_str] When parsing Reply: cannot find msg{seg.id}. Info: {e}')
                    result += f"Reply(id='{seg.id}' error: cannot find this message.)"
            case Face():
                result += f"Face(id='{seg.id}', resultId='{seg.resultId}', chainCount={seg.chainCount}, {get_qface_info(int(seg.id))})"
            case File():
                result += f"File(file_name={seg.file}, url={seg.url})"
            case _:
                result += str(seg)
    return result

def parse_msg_to_list(msg: tuple[Message | UnknownMessageSegment, ...]) -> list[ChatCompletionContentPartParam]:
    return [{"type": "text", "text": parse_msg_to_str(msg)}]

def get_qface(emojiId: int) -> QFace:
    result: QFace|None = _qface_cache.get(emojiId)
    if result is None:
        for face in qface:
            if int(face["emojiId"]) == emojiId:
                result = face
                _qface_cache[emojiId] = face
                break
        if result is None:
            log.error(f"[qmessage->get_qface] Can not get face with id {emojiId}!")
            raise RuntimeError(f"[qmessage->get_qface] Can not get face with id {emojiId}!")
    return result

def get_qface_info(id: int) -> str:
    face = get_qface(id)
    paths: list[Path] = []
    for asset in face["assets"]:
        paths.append(_qface_base_path/Path(asset["path"]))
    return f"description=`{face['describe']}`, assets-path=`{str(paths)}`"

_qface_cache: dict[int, QFace] = {}

try:
    qface: list[QFace] = json.loads(_qface_index_path.read_text(encoding='utf-8'))
except (OSError, json.JSONDecodeError) as e:
    log.error(f"[qmessage->qface] Failed to load qface index: {e}")
    qface = []
