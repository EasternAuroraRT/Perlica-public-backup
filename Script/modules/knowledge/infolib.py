from llama_index.core import Settings, StorageContext, load_index_from_storage
from llama_index.embeddings.ollama import OllamaEmbedding
from pathlib import Path
import os

from modules.core.logger import log
import modules.knowledge.infolib_init as infolib

# ======== Config ======== #

src_path = infolib.src_path
dst_path = infolib.dst_path
# ========================= #

embed_model = OllamaEmbedding(
    model_name=infolib.model_name,
    base_url=infolib.base_url,
)

Settings.embed_model = embed_model

# 缓存变量（用于热更新）
_index_cache = None
_last_mtime_cache = 0.0

def search_knowledge_base(query: str, top_k: int = 3) -> list:
    global _index_cache, _last_mtime_cache

    def get_dir_mtime(path: Path) -> float:
        if not path.exists():
            return 0.0
        max_mtime = 0.0
        for root, _, files in os.walk(path):
            for f in files:
                filepath = os.path.join(root, f)
                try:
                    mtime = os.path.getmtime(filepath)
                    if mtime > max_mtime:
                        max_mtime = mtime
                except OSError as e:
                    log.error(f"[infolib->get_dir_mtime] {e}")
                    continue
        return max_mtime

    current_mtime = get_dir_mtime(dst_path)

    # 热更新检测
    if current_mtime > _last_mtime_cache or _index_cache is None:
        try:
            storage_context = StorageContext.from_defaults(persist_dir=str(dst_path))
            _index_cache = load_index_from_storage(storage_context)
            _last_mtime_cache = current_mtime
        except Exception as e:
            _index_cache = None
            return [{"type": "text", "text": f"知识库加载失败: {str(e)}"}]

    if _index_cache is None:
        return [{"type": "text", "text": "知识库未初始化，请运行 init_kb.py 或检查存储目录"}]

    retriever = _index_cache.as_retriever(similarity_top_k=top_k)

    try:
        nodes_with_scores = retriever.retrieve(query)
        if not nodes_with_scores:
            return [{"type": "text", "text": "未找到相关信息"}]

        formatted_contexts = []
        for node_with_score in nodes_with_scores:
            node = node_with_score.node
            metadata = node.metadata

            path_info = metadata.get("path_hierarchy", "未知路径")

            title_parts = []
            for i in range(1, 5):
                key = f"header_{i}"
                if key in metadata and metadata[key]:
                    title_parts.append(metadata[key])
            title_chain = " > ".join(title_parts) if title_parts else "无章节信息"

            # ----- 关键修复：使用 get_content() 方法 -----
            content = node.get_content()   # BaseNode 标准方法，无属性访问问题

            block = f"""【文档路径】: {path_info}
【所属章节】: {title_chain}
【内容片段】:
{content}
"""
            formatted_contexts.append(block)

        combined = "\n\n---\n\n".join(formatted_contexts)
        return [{"type": "text", "text": combined}]

    except Exception as e:
        return [{"type": "text", "text": f"查询失败: {str(e)}"}]