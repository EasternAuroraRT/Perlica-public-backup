import os
from pathlib import Path
from llama_index.core import Settings, SimpleDirectoryReader, VectorStoreIndex
from llama_index.core.node_parser import MarkdownNodeParser  # 关键导入
from llama_index.embeddings.ollama import OllamaEmbedding

src_path = Path(__file__).parent / "EndfieldLibrary"
dst_path = Path(__file__).parent / "storage"

model_name = "qwen-embed"
base_url = "http://localhost:11434"

if __name__ == "__main__":
    embed_model = OllamaEmbedding(
        model_name=model_name,
        base_url=base_url,
    )
    Settings.embed_model = embed_model

    # 1. 依然注入路径元数据（方案一的好处保留）
    def custom_file_metadata(file_path: str):
        p = Path(file_path)
        return {
            "file_name": p.name,
            "path_hierarchy": " > ".join(p.parts[-3:]),
            "full_path": str(p)
        }

    reader = SimpleDirectoryReader(
        src_path, 
        recursive=True,
        file_metadata=custom_file_metadata
    )
    documents = reader.load_data()

    # 2. 使用 Markdown 解析器（替换默认的 SentenceSplitter）
    parser = MarkdownNodeParser()
    # 解析文档生成节点（此时节点包含了父子关系，且 node.metadata 里会继承标题）
    nodes = parser.get_nodes_from_documents(documents)

    # 3. 构建索引（直接用节点列表）
    index = VectorStoreIndex(nodes)
    index.storage_context.persist(dst_path)

    print("知识库初始化完成（已解析 Markdown 标题层级）")