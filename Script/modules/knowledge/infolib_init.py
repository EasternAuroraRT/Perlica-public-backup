import json
from pathlib import Path

from llama_index.core import (
    Settings,
    SimpleDirectoryReader,
    StorageContext,
    VectorStoreIndex,
    load_index_from_storage,
)
from llama_index.core.node_parser import MarkdownNodeParser, SentenceSplitter
from llama_index.core.schema import TextNode
from llama_index.embeddings.ollama import OllamaEmbedding

src_path = Path(__file__).parent.parent / "EndfieldLibrary/rag_source/fz_wiki_data_rag/documents"
dst_path = Path(__file__).parent.parent / "storage"

model_name = "qwen-embed"
base_url = "http://localhost:11434"

# 单个节点嵌入的最大 token 数，超过则二次切分，避免 Ollama 显存不足（CUDA OOM）
MAX_CHUNK_TOKENS = 512
CHUNK_OVERLAP = 50

# 源文件状态清单，用于对比出增量变更
MANIFEST_FILE = dst_path / "manifest.json"


def get_embed_model() -> OllamaEmbedding:
    return OllamaEmbedding(
        model_name=model_name,
        base_url=base_url,
    )


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


def full_build() -> None:
    file_paths = [src_path / rel for rel in sorted(scan_sources())]
    nodes = parse_nodes(load_documents(file_paths))
    index = VectorStoreIndex(nodes)
    index.storage_context.persist(dst_path)


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


def incremental_build(added: list, deleted: list, modified: list) -> None:
    storage_context = StorageContext.from_defaults(persist_dir=str(dst_path))
    index = load_index_from_storage(storage_context)

    # 删除与修改的文件先移除旧节点，再插入新节点
    for rel in deleted + modified:
        index.delete_ref_doc(rel, delete_from_docstore=True)

    changed = added + modified
    if changed:
        nodes = parse_nodes(load_documents([src_path / rel for rel in sorted(changed)]))
        index.insert_nodes(nodes)

    index.storage_context.persist(dst_path)


def main() -> None:
    Settings.embed_model = get_embed_model()
    dst_path.mkdir(parents=True, exist_ok=True)

    sources = scan_sources()
    manifest = load_manifest()
    index_exists = (dst_path / "index_store.json").exists()

    if manifest and index_exists:
        added, deleted, modified = diff_sources(sources, manifest)
        if not (added or deleted or modified):
            print("知识库无变更，跳过重建")
            return
        try:
            incremental_build(added, deleted, modified)
        except Exception as e:
            print(f"增量更新失败，回退为全量重建: {e}")
            full_build()
        save_manifest(sources)
        print(f"知识库增量更新完成（新增 {len(added)}，修改 {len(modified)}，删除 {len(deleted)}）")
    else:
        full_build()
        save_manifest(sources)
        print("知识库初始化完成（已解析 Markdown 标题层级）")


if __name__ == "__main__":
    main()
