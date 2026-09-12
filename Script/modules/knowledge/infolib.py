from __future__ import annotations
from typing import * # pyright: ignore[reportWildcardImportFromLibrary]
import gc
import json
import threading
import time

import numpy as np

from modules.core.logger import log
from config import config
import modules.knowledge.infolib_init as infolib

# ======== Config ======== #

src_path = infolib.src_path
dst_path = infolib.dst_path
# ========================= #

# ---------- 紧凑磁盘索引缓存 ----------
# 向量矩阵通过 np.memmap 只读映射，页面可被内核回收，不常驻 RSS；
# 节点记录（text + metadata）体积很小，直接放内存。

_load_lock = threading.Lock()
_records: list[dict] = []                 # 与向量矩阵行序一致
_matrix: Optional[np.memmap] = None       # float32 (N, dim)，只读映射
_store_mtime_cache: float = 0.0
_load_error: Optional[str] = None         # 最近一次失败原因（供日志 / 返回给调用方）
_next_attempt: float = 0.0                # 加载失败后的冷却截止时刻

_RETRY_COOLDOWN = 60.0                    # 普通失败：1 分钟内不再重试
_MEMORY_COOLDOWN = 600.0                  # OOM：10 分钟内不再尝试
_MIN_MEM_TO_LOAD = 2 * 1024 ** 3          # 加载节点记录前要求至少 2 GiB 空闲内存

_STORE_FILES = ("meta.json", "nodes.jsonl", "embeddings.bin")


def _store_mtime() -> float:
    """产物文件中最新的 mtime；不存在时返回 0。"""
    max_mtime = 0.0
    for name in _STORE_FILES:
        try:
            max_mtime = max(max_mtime, (dst_path / name).stat().st_mtime)
        except OSError:
            continue
    return max_mtime


def _mem_available_bytes() -> float:
    """读取 /proc/meminfo 的 MemAvailable；读取失败时返回无穷大（不拦截）。"""
    try:
        with open("/proc/meminfo", encoding="utf-8") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return float(line.split()[1]) * 1024.0
    except (OSError, ValueError, IndexError):
        pass
    return float("inf")


def _drop_cache() -> None:
    """释放已加载的索引（关闭 mmap 并回收）。"""
    global _records, _matrix, _load_error
    if _matrix is not None:
        try:
            mm = _matrix._mmap  # type: ignore[union-attr]
            if mm is not None:
                mm.close()
        except Exception:
            pass
    _records = []
    _matrix = None
    _load_error = None
    gc.collect()


def load_knowledge_index() -> bool:
    """把紧凑索引读入进程（线程安全、失败冷却）。

    只读映射 embeddings.bin（不复制进内存），并加载小体积的 nodes.jsonl。
    """
    global _records, _matrix, _store_mtime_cache, _load_error, _next_attempt

    if _matrix is not None:
        return True

    now = time.monotonic()
    if now < _next_attempt:
        return False

    if not (dst_path / "meta.json").exists():
        _load_error = "知识库索引不存在，请先运行 `python -m modules.knowledge.infolib_init` 构建"
        _next_attempt = now + _RETRY_COOLDOWN
        log.error(f"[infolib->load_knowledge_index] {_load_error}")
        return False

    if _mem_available_bytes() < _MIN_MEM_TO_LOAD:
        _load_error = "系统空闲内存不足（<2 GiB），放弃加载知识库索引以避免 OOM"
        _next_attempt = now + _MEMORY_COOLDOWN
        log.error(f"[infolib->load_knowledge_index] {_load_error}")
        return False

    with _load_lock:
        if _matrix is not None:
            return True
        if now < _next_attempt:
            return False

        try:
            meta = json.loads((dst_path / "meta.json").read_text(encoding="utf-8"))
            dim = int(meta.get("dim", 0))
            count = int(meta.get("count", 0))
            if dim <= 0 or count <= 0:
                raise RuntimeError(f"索引 meta 不完整: {meta}")

            matrix = np.memmap(
                dst_path / "embeddings.bin", dtype="<f4", mode="r",
                shape=(count, dim),
            )

            records: list[dict] = []
            with open(dst_path / "nodes.jsonl", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        records.append(json.loads(line))
            if len(records) != count:
                raise RuntimeError(
                    f"节点记录数与向量行数不一致: {len(records)} vs {count}")
        except MemoryError as e:
            _load_error = f"加载知识库索引内存不足（OOM）：{e}"
            _next_attempt = time.monotonic() + _MEMORY_COOLDOWN
            log.error(f"[infolib->load_knowledge_index] {_load_error}")
            gc.collect()
            return False
        except Exception as e:
            _load_error = f"加载知识库索引失败：{e}"
            _next_attempt = time.monotonic() + _RETRY_COOLDOWN
            log.error(f"[infolib->load_knowledge_index] {_load_error}")
            _drop_cache()
            return False

        _records = records
        _matrix = matrix
        _store_mtime_cache = _store_mtime()
        _load_error = None
        _next_attempt = 0.0
        log.info(f"[infolib] 知识库索引加载完成: {count} 节点 × {dim} 维（mmap）")
        return True


def _ensure_index() -> bool:
    """确保索引已加载且与磁盘版本一致；不可用时在 _load_error 中说明原因。"""
    global _matrix, _store_mtime_cache

    current_mtime = _store_mtime()
    if _matrix is not None and current_mtime <= _store_mtime_cache:
        return True

    if _matrix is not None:
        # 磁盘上的索引被重新构建过：先释放旧映射，再加载新的一份
        log.info("[infolib] 检测到知识库索引已更新，重新加载……")
        _drop_cache()

    return load_knowledge_index()


def reload_knowledge_index() -> bool:
    """强制释放并重新加载索引（知识库重建后调用）。"""
    _drop_cache()
    return load_knowledge_index()


def _query_request_dim(meta_dim: int) -> int | None:
    """构造查询时请求的维度：仅当存量确实是 EMBED_DIM 降维时再请求降维，
    否则让模型输出默认维度，与矩阵列数保持一致。"""
    if meta_dim == infolib.EMBED_DIM:
        return infolib.EMBED_DIM
    return None


def search_knowledge_base(query: str, top_k: int = 3) -> list:
    global _matrix, _load_error, _next_attempt

    if not config.enable_rag:
        return [{"type": "text", "text": f"知识库不可用: 功能未启用"}]

    if not _ensure_index():
        return [{"type": "text", "text": f"知识库不可用: {_load_error or '加载失败'}"}]

    if _matrix is None or not _records:
        return [{"type": "text", "text": f"知识库未加载: {_load_error or '未知原因'}"}]

    try:
        matrix: np.memmap = _matrix
        meta_dim = int(matrix.shape[1])
        request_dim = _query_request_dim(meta_dim)

        query_vec = np.asarray(
            infolib.embed_texts([query], request_dim)[0], dtype="<f4")
        if query_vec.size != meta_dim:
            return [{"type": "text", "text":
                     f"查询向量维度 {query_vec.size} 与索引维度 {meta_dim} 不一致，"
                     "请重新构建知识库索引"}]

        norm = float(np.sqrt(float((query_vec * query_vec).sum())))
        if norm == 0.0:
            norm = 1.0
        query_vec = query_vec / norm

        # 行向量已归一化：相似度 = 矩阵 @ 查询向量
        sims = np.asarray(matrix @ query_vec, dtype=np.float32)

        k = max(1, min(int(top_k), len(_records)))
        # top-k 索引（不排序全量，只挑出最大的 k 个）
        if k >= len(sims):
            top_indices = np.arange(len(sims))
        else:
            top_indices = np.argpartition(-sims, k - 1)[:k]
        top_indices = top_indices[np.argsort(-sims[top_indices])]

        blocks = []
        for idx in top_indices:
            rec = _records[int(idx)]
            metadata = rec.get("metadata", {}) or {}
            text = rec.get("text", "")

            path_info = metadata.get("path_hierarchy", "未知路径")
            title_parts = []
            for i in range(1, 5):
                key = f"header_{i}"
                if key in metadata and metadata[key]:
                    title_parts.append(str(metadata[key]))
            title_chain = " > ".join(title_parts) if title_parts else "无章节信息"

            blocks.append(f"""【文档路径】: {path_info}
【所属章节】: {title_chain}
【内容片段】:
{text}
""")

        if not blocks:
            return [{"type": "text", "text": "未找到相关信息"}]
        return [{"type": "text", "text": "\n\n---\n\n".join(blocks)}]

    except MemoryError:
        log.error("[infolib->search_knowledge_base] 检索过程内存不足（OOM）")
        _load_error = "检索知识库时内存不足，已进入冷却；索引过大，建议精简后重建"
        _next_attempt = time.monotonic() + _MEMORY_COOLDOWN
        _drop_cache()
        gc.collect()
        return [{"type": "text", "text": "知识库检索内存不足，本次已跳过。请精简索引后重试。"}]

    except Exception as e:
        return [{"type": "text", "text": f"查询失败: {str(e)}"}]
