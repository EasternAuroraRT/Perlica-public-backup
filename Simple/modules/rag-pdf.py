# This module is not finished and not in use.
import os
import torch
from PIL import Image
from pdf2image import convert_from_path
from transformers import AutoModel, AutoProcessor
from qwen_vl_utils import process_vision_info
import pickle
from pathlib import Path

# ==================== 配置 ====================
MODEL_NAME = "Qwen/Qwen3-VL-Embedding-2B"  # 或使用本地路径
PDF_PATH = "./data/your_document.pdf"      # PDF 文件路径
INDEX_SAVE_PATH = "./storage/index.pkl"    # 索引保存路径
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
# =============================================

# 1. 加载模型和处理器
print("Loading model...")
model = AutoModel.from_pretrained(
    MODEL_NAME,
    torch_dtype=torch.bfloat16 if DEVICE == "cuda" else torch.float32,
    trust_remote_code=True,
    device_map=DEVICE
).eval()
processor = AutoProcessor.from_pretrained(MODEL_NAME, trust_remote_code=True)

# 2. PDF 转图片 (每页一张)
print(f"Converting PDF to images: {PDF_PATH}")
images = convert_from_path(PDF_PATH, dpi=144)  # dpi 控制分辨率[reference:2]
print(f"Total pages: {len(images)}")

# 3. 生成每页的 Embedding
page_embeddings = []
page_images = []

for idx, img in enumerate(images):
    # 保存临时图片 (也可直接传入 PIL Image)
    temp_path = f"/tmp/page_{idx}.png"
    img.save(temp_path)
    page_images.append(temp_path)

    # 构造多模态输入
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "image", "image": temp_path},
                {"type": "text", "text": "Represent this page for retrieval."}  # 可自定义指令[reference:3]
            ]
        }
    ]
    
    # 处理输入并生成 embedding
    text = processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    inputs = processor(
        text=[text],
        images=[Image.open(temp_path)],
        padding=True,
        return_tensors="pt"
    ).to(DEVICE)
    
    with torch.no_grad():
        embedding = model(**inputs).last_hidden_pool  # 输出向量
        # 如果需要 L2 归一化（推荐用于余弦相似度检索）[reference:4]
        embedding = torch.nn.functional.normalize(embedding, p=2, dim=-1)
        page_embeddings.append(embedding.cpu().numpy())

# 4. 保存索引到磁盘
index_data = {
    "embeddings": page_embeddings,  # List of numpy arrays
    "page_images": page_images,     # 图片路径列表
    "total_pages": len(images)
}
with open(INDEX_SAVE_PATH, "wb") as f:
    pickle.dump(index_data, f)

print(f"✅ Index built successfully! Saved to {INDEX_SAVE_PATH}")
print(f"Total pages indexed: {len(page_embeddings)}")