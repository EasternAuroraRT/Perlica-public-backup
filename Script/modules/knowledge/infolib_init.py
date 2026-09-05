"""
知识库构建（v2：磁盘型紧凑存储 + 可选 MRL 降维）
====================================================

对比 v1（llama-index 整库 JSON 持久化，4GB+ 且查询时整包载入内存）：
- 向量矩阵改为 float32 原始二进制 `embeddings.bin`（文件大小约为旧 JSON 的 1/25）；
- 节点文本/元数据写入 `nodes.jsonl`（替代 475MB docstore）；
- 构建时向 Ollama 请求 MRL 降维（EMBED_DIM 维，若模型支持；不支持则回退默认维度）；
- 检索侧用 np.memmap 只读映射向量，不再把整库载入 RAM。

产物（均在 dst_path 下）：
    embeddings.bin   float32 little-endian, shape (N, dim)，行序与 nodes.jsonl 一致
    nodes.jsonl      每行 {"rel": 源文件相对路径, "text": 分块文本, "metadata": {...}}
    meta.json        {"model","dim","count","request_dim"}
    manifest.json    源文件 mtime/size 清单（增量判断，格式与 v1 一致）

用法：
    # 首次迁移（会删除旧版 4GB 文件），建议先停 bot 再构建：
    python -m modules.knowledge.infolib_init        # 首次：全量构建；之后：增量更新
"""

# Pylance 对本文件偶发整墙报 builtins(list/str/int/print...)未定义（误报）。
# 该文件可被解释器正常执行；如确需恢复该检查，删除下一行并 Reload Window 即可。
# pyright: reportUndefinedVariable=false

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
from ollama import Client

from llama_index.core import SimpleDirectoryReader
from llama_index.core.node_parser import MarkdownNodeParser, SentenceSplitter
from llama_index.core.schema import TextNode

src_path = Path(__file__).parent.parent.parent / "EndfieldLibrary/rag_source/fz_wiki_data_rag/documents"
dst_path = Path(__file__).parent.parent.parent / "EndfieldLibrary/storage"

model_name = "qwen-embed"
base_url = "http://localhost:11434"

# 单个节点嵌入的最大 token 数，超过则二次切分，避免 Ollama 显存不足（CUDA OOM）
MAX_CHUNK_TOKENS = 512
CHUNK_OVERLAP = 50

# MRL 目标维度。模型支持（Qwen3-Embedding 等）时以该维度嵌入/检索，文件与内存同步缩小；
# 不支持时自动回退模型默认输出维度。改为 None 可禁用降维。
EMBED_DIM = 512

# 源文件状态清单，用于对比出增量变更
MANIFEST_FILE = dst_path / "manifest.json"

# 产出文件名
EMBED_BIN = dst_path / "embeddings.bin"
NODES_JSONL = dst_path / "nodes.jsonl"
META_JSON = dst_path / "meta.json"

# 旧版 llama-index 遗留文件，构建成功后清理以回收 ~4.1GB 磁盘
_LEGACY_FILES = (
    "default__vector_store.json",
    "docstore.json",
    "index_store.json",
    "graph_store.json",
    "image__vector_store.json",
)

_EMBED_BATCH = 32          # 单次向 Ollama 请求的文本条数
_KEEP_ALIVE = "10m"

_client = Client(host=base_url)


# ==================== 嵌入 ====================

def embed_texts(texts: list[str], dimensions: int | None = None,
                batch_size: int = _EMBED_BATCH) -> list[list[float]]:
    """分批请求 Ollama 嵌入。dimensions 不支持时会在调用处抛错（由 probe 决定回退）。

    刻意不做"失败自动降维重试"：若构建中途某批降维成功、某批失败回退全维度，
    会产出维度不一致的矩阵。
    """
    out: list[list[float]] = []
    for start in range(0, len(texts), batch_size):
        chunk = texts[start:start + batch_size]
        resp = _client.embed(
            model=model_name, input=chunk, dimensions=dimensions,
            keep_alive=_KEEP_ALIVE,
        )
        out.extend(list(v) for v in resp.embeddings)
    return out


def probe_request_dim() -> int | None:
    """探测模型是否支持 MRL 降维到 EMBED_DIM。

    返回实际应请求的 dimensions（支持则 EMBED_DIM，否则 None=默认全维度）。
    """
    try:
        v = embed_texts(["qbot dimension probe"], EMBED_DIM)
        actual = len(v[0])
        if actual == EMBED_DIM:
            return EMBED_DIM
        print(f"[infolib_init] 模型 {model_name} 实际输出维度 {actual}，"
              f"与 EMBED_DIM={EMBED_DIM} 不同，本次构建不降维。")
        return None
    except Exception as e:
        print(f"[infolib_init] 模型 {model_name} 不接受 dimensions={EMBED_DIM}"
              f"（{e}），改用默认维度。")
        return None


# ==================== 自定义文件元数据 ====================

def custom_file_metadata(file_path: str) -> dict:
    p = Path(file_path)
    return {
        "file_name": p.name,
        "path_hierarchy": " > ".join(p.parts[-3:]),
        "full_path": str(p),
    }


def _make_doc_id(file_path: Path) -> str:
    # 以相对路径作为文档唯一 id，使增量更新能精确定位到具体文件
    return file_path.relative_to(src_path).as_posix()


# ==================== 源文件扫描 / manifest ====================

def scan_sources() -> dict:
    """扫描源目录，返回 {相对路径: {mtime_ns, size}}。"""
    sources = {}
    if not src_path.exists():
        return sources
    for p in sorted(src_path.rglob("*")):
        if p.is_file():
            st = p.stat()
            sources[p.relative_to(src_path).as_posix()] = {
                "mtime_ns": st.st_mtime_ns,
                "size": st.st_size,
            }
    return sources


def load_manifest() -> dict:
    if not MANIFEST_FILE.exists():
        return {}
    try:
        return json.loads(MANIFEST_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_manifest(manifest: dict) -> None:
    dst_path.mkdir(parents=True, exist_ok=True)
    MANIFEST_FILE.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def diff_sources(sources: dict, manifest: dict) -> tuple[list, list, list]:
    added = sorted(rel for rel in sources if rel not in manifest)
    deleted = sorted(rel for rel in manifest if rel not in sources)
    modified = sorted(
        rel
        for rel in sources
        if rel in manifest
        and (
            sources[rel]["mtime_ns"] != manifest[rel].get("mtime_ns")
            or sources[rel]["size"] != manifest[rel].get("size")
        )
    )
    return added, deleted, modified


# ==================== 文档解析（与 v1 保持一致，节点数量不变） ====================

def load_documents(file_paths: list[Path]) -> list:
    """加载指定文件为文档，并注入路径元数据与确定性 doc_id（与原逻辑一致）。"""
    documents = []
    for fp in file_paths:
        reader = SimpleDirectoryReader(
            input_files=[str(fp)],
            file_metadata=custom_file_metadata,
        )
        for doc in reader.load_data():
            doc.doc_id = _make_doc_id(fp)
            documents.append(doc)
    return documents


def parse_nodes(documents) -> list:
    # 使用 Markdown 解析器（替换默认的 SentenceSplitter）
    parser = MarkdownNodeParser()
    nodes = parser.get_nodes_from_documents(documents)
    return _split_oversized_nodes(nodes)


def _split_oversized_nodes(nodes: list) -> list:
    """将超过 MAX_CHUNK_TOKENS 的节点二次切分，保留元数据与 SOURCE 关系（ref_doc_id）。"""
    splitter = SentenceSplitter(chunk_size=MAX_CHUNK_TOKENS, chunk_overlap=CHUNK_OVERLAP)
    result = []
    for node in nodes:
        chunks = splitter.split_text(node.get_content())
        if len(chunks) <= 1:
            result.append(node)
            continue
        for text in chunks:
            result.append(TextNode(
                text=text,
                metadata=dict(node.metadata),
                relationships=dict(node.relationships),
            ))
    return result


def _records_from_file(fp: Path) -> list[dict]:
    """解析单个源文件为检索记录，记录以文件相对路径 rel 作为归属标记。"""
    rel = fp.relative_to(src_path).as_posix()
    docs = load_documents([fp])
    nodes = parse_nodes(docs)
    recs: list[dict] = []
    for n in nodes:
        recs.append({
            "rel": rel,
            "text": n.get_content(),
            "metadata": dict(n.metadata),
        })
    return recs


# ==================== 存储读写 ====================

def get_store_meta() -> dict:
    if not META_JSON.exists():
        return {}
    try:
        meta = json.loads(META_JSON.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(meta, dict):
        return {}
    return meta


def store_exists() -> bool:
    meta = get_store_meta()
    return bool(meta and EMBED_BIN.exists() and NODES_JSONL.exists())


def _normalize_rows(matrix: np.ndarray) -> np.ndarray:
    m = np.asarray(matrix, dtype=np.float32)
    norms = np.sqrt((m * m).sum(axis=1, keepdims=True))
    norms[norms == 0.0] = 1.0
    return m / norms


def _save_store(records: list[dict], matrix: np.ndarray, request_dim: int | None) -> None:
    """把记录与向量矩阵原子写入磁盘（先写临时文件再 rename）。"""
    dst_path.mkdir(parents=True, exist_ok=True)
    matrix = _normalize_rows(np.ascontiguousarray(matrix, dtype=np.float32))

    tmp_bin = dst_path / "embeddings.bin.tmp"
    tmp_jsonl = dst_path / "nodes.jsonl.tmp"
    tmp_meta = dst_path / "meta.json.tmp"

    matrix.tofile(tmp_bin)
    with open(tmp_jsonl, "w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    meta = {
        "version": 2,
        "model": model_name,
        "request_dim": request_dim,
        "dim": int(matrix.shape[1]),
        "count": len(records),
    }
    tmp_meta.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    tmp_bin.replace(EMBED_BIN)
    tmp_jsonl.replace(NODES_JSONL)
    tmp_meta.replace(META_JSON)


def _load_existing() -> tuple[list[dict], np.ndarray | None, dict]:
    """读取现有 v2 存储（供增量更新使用）。无存储时返回空。"""
    meta = get_store_meta()
    if not meta or not EMBED_BIN.exists() or not NODES_JSONL.exists():
        return [], None, {}

    records = []
    with open(NODES_JSONL, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))

    dim = int(meta.get("dim", 0))
    count = int(meta.get("count", 0))
    matrix: np.ndarray | None = None
    if dim > 0 and count > 0 and count == len(records):
        mm = np.memmap(EMBED_BIN, dtype="<f4", mode="r", shape=(count, dim))
        matrix = np.array(mm, dtype=np.float32, copy=True)
        try:
            inner = mm._mmap  # type: ignore[union-attr]
            if inner is not None:
                inner.close()
        except Exception:
            pass
    return records, matrix, meta


def _clean_legacy() -> None:
    """构建成功后清理 v1 llama-index 遗留文件，回收磁盘。"""
    for name in _LEGACY_FILES:
        p = dst_path / name
        try:
            if p.exists():
                p.unlink()
                print(f"[infolib_init] 已清理遗留文件: {p.name}")
        except OSError as e:
            print(f"[infolib_init] 清理 {name} 失败（可手动删除）: {e}")


# ==================== 构建 ====================

def full_build(request_dim: int | None) -> None:
    """全量构建：解析全部文档并嵌入，覆盖写整个存储。"""
    all_paths = sorted(src_path / rel for rel in scan_sources())
    if not all_paths:
        raise RuntimeError(f"源目录为空: {src_path}")

    print(f"== 全量构建: {len(all_paths)} 个文件, 目标维度={request_dim or '默认'} ==")
    started = time.time()

    records: list[dict] = []
    for fp in all_paths:
        records.extend(_records_from_file(fp))
    print(f"   切块节点数: {len(records)}")

    vec_rows: list[np.ndarray] = []
    block = 200
    for i in range(0, len(records), block):
        sub = records[i:i + block]
        vecs = embed_texts([r["text"] for r in sub], request_dim)
        vec_rows.extend(np.asarray(v, dtype=np.float32) for v in vecs)
        if (i // block + 1) % 5 == 0 or i + block >= len(records):
            print(f"   已嵌入 {min(i + block, len(records))}/{len(records)}"
                  f"（{time.time() - started:.0f}s）")

    if not records:
        raise RuntimeError("构建失败：没有生成任何检索节点")
    matrix = np.stack(vec_rows, axis=0)
    _save_store(records, matrix, request_dim)
    save_manifest(scan_sources())
    _clean_legacy()
    print(f"== 全量构建完成: {len(records)} 节点, 维度 {matrix.shape[1]}, "
          f"耗时 {time.time() - started:.0f}s ==")


def incremental_build(added: list, deleted: list, modified: list, request_dim: int | None) -> None:
    """增量更新：复用未变动文档的向量，仅重嵌新增/修改文件，移除删除的。"""
    records, matrix, _ = _load_existing()
    if matrix is None:
        raise RuntimeError("无 v2 存储可增量更新，请先执行全量构建")

    print(f"== 增量更新: 新增 {len(added)}, 修改 {len(modified)}, 删除 {len(deleted)} ==")
    started = time.time()

    drop_rel = set(deleted) | set(modified)
    keep = [(i, r) for i, r in enumerate(records) if r["rel"] not in drop_rel]
    keep_idx = [i for i, _ in keep]
    keep_records = [r for _, r in keep]
    keep_matrix = matrix[keep_idx] if keep_idx else np.empty((0, matrix.shape[1]), dtype=np.float32)

    changed = sorted(set(added) | set(modified))
    new_records: list[dict] = []
    new_rows: list[np.ndarray] = []
    for rel in changed:
        fp = src_path / rel
        if not fp.exists():
            continue
        file_recs = _records_from_file(fp)
        if file_recs:
            vecs = embed_texts([r["text"] for r in file_recs], request_dim)
            new_records.extend(file_recs)
            new_rows.extend(np.asarray(v, dtype=np.float32) for v in vecs)
        print(f"   已处理 {rel}（累计 {len(new_records)} 新节点，{time.time() - started:.0f}s）")

    if new_rows:
        new_matrix = np.stack(new_rows, axis=0)
        if new_matrix.shape[1] != keep_matrix.shape[1]:
            raise RuntimeError(
                f"新向量维度 {new_matrix.shape[1]} 与存量 {keep_matrix.shape[1]} 不一致，"
                "请删除 storage 目录后全量重建")
        all_matrix = np.concatenate([keep_matrix, new_matrix], axis=0)
    else:
        all_matrix = keep_matrix
    all_records = keep_records + new_records

    if not all_records:
        raise RuntimeError("增量更新后没有任何节点")
    _save_store(all_records, all_matrix, request_dim)
    save_manifest(scan_sources())
    print(f"== 增量更新完成: {len(all_records)} 节点, 维度 {all_matrix.shape[1]}, "
          f"耗时 {time.time() - started:.0f}s ==")


def main() -> None:
    dst_path.mkdir(parents=True, exist_ok=True)

    sources = scan_sources()
    if not sources:
        print(f"源目录为空: {src_path}")
        sys.exit(1)

    if not store_exists():
        print("检测到尚无 v2 存储，开始全量构建……")
        request_dim = probe_request_dim()
        print(f"请求维度: {request_dim if request_dim is not None else '默认'}")
        full_build(request_dim)
        return

    manifest = load_manifest()
    added, deleted, modified = diff_sources(sources, manifest)
    if not (added or deleted or modified):
        print("知识库无变更，跳过重建")
        return

    request_dim = probe_request_dim()
    print(f"请求维度: {request_dim if request_dim is not None else '默认'}")
    try:
        incremental_build(added, deleted, modified, request_dim)
    except Exception as e:
        print(f"增量更新失败，回退为全量重建: {e}")
        full_build(request_dim)


if __name__ == "__main__":
    main()
