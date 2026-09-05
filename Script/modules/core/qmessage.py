from __future__ import annotations
import asyncio
import json
from typing import * # pyright: ignore[reportWildcardImportFromLibrary]
from pathlib import Path
from openai.types.chat import * # pyright: ignore[reportWildcardImportFromLibrary]

from napcat import * # pyright: ignore[reportWildcardImportFromLibrary]
from . import env
import config
from . import history
from .logger import log
from ..tools.impl.image_processor import get_image_base64_from_url, get_image_base64_from_path

_qface_base_path: Path = Path(__file__).parent.parent.parent/"Qface"
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

def parse_msgseg_to_chatseg(seg: Message|UnknownMessageSegment, multimodal: bool = False, file_ability: bool = False) -> ChatCompletionContentPartParam:
    match seg:
        case Text():
            return {"type": "text", "text": seg.text}
        case Reply():
            try:
                assert(seg.id)
                history_msg = history.get_message_by_id(int(seg.id))
                if not history_msg:
                    raise RuntimeError(f"get_message_by_id({seg.id}) returns None.")
                return {"type": "text", "text": f"Reply(id='{seg.id}', raw={history_msg})\n"}
            except Exception as e:
                log.warning(f'[qmessage->parse_msgseg_to_chatseg] When parsing Reply: cannot find msg{seg.id}. Info: {e}')
                return {"type": "text", "text": f"Reply(id='{seg.id}' error: cannot find this message.)\n"}
        case Face():
            if not multimodal:
                return {"type": "text", "text": f"Face(id='{seg.id}', resultId='{seg.resultId}', chainCount={seg.chainCount}, {get_qface_info(int(seg.id))})"}
            face_info = get_qface(int(seg.id))
            __raw_path: Optional[Path] = None
            __fallback: Optional[Path] = None
            for asset in face_info["assets"]:
                if asset["type"] == 2 and not __raw_path:
                    __raw_path = _qface_base_path/Path(asset["path"])
                    break
                elif asset['type'] == 0 and not __fallback:
                    __fallback = _qface_base_path/Path(asset["path"])
                else: continue
            if not __raw_path:
                __raw_path = __fallback
            try:
                assert(__raw_path)
                image_b64: str = get_image_base64_from_path(str(__raw_path))
                return {"type": "image_url", "image_url": {"url": image_b64}}
            except Exception as e:
                log.error(f"[{__file__}->parse_msgseg_to_chatseg] Error occurred when parsing QFace {seg.id}: {e}")
                return {"type": "text", "text": f"Face({get_qface_info(int(seg.id))}, warning: image file is not available)\n"}
        case File():
            return {"type": "text", "text": f"File(file_name={seg.file}, url={seg.url})"}
        case Poke():
            return {"type": "text", "text": str(seg)}
        case Image():
            if seg.url is None:
                return {"type": "text", "text": f"`Image({seg.file})`, url is not available.\n"}
            return {"type": "image_url", "image_url": {"url": seg.url}}
        case _:
            return {"type": "text", "text": str(seg)}

def parse_msg_to_str(msg: tuple[Message | UnknownMessageSegment, ...]) -> str:
    return str(msg)

def parse_msg_to_list(msg: tuple[Message | UnknownMessageSegment, ...], multimodal: bool = False) -> list[ChatCompletionContentPartParam]:
    result: list[ChatCompletionContentPartParam] = []
    for msgseg in msg:
        result.append(parse_msgseg_to_chatseg(msgseg, multimodal=multimodal))
    return result

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
