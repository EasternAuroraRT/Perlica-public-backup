# fz_wiki_sync —— 终末地 Wiki 数据同步工具

把 [终末地 Wiki（https://fz.wiki）](https://fz.wiki) 的核心数据自动同步到本地，
**镜像 wiki 数据库的实际排布**（文章 + 修订），不抓取 HTML。

## 数据来源（关键结论）

`fz.wiki` 是 Next.js 动态站点，页面 HTML 里没有有效内容（直接爬 HTML 拿不到数据）。
前端 JS 中暴露了后端 API 地址：

```
https://api.fz.wiki/api/v1/...
```

前端所有数据（文章列表、正文、修订、搜索）都来自这个后端。本工具直接调用该 API，
获取与数据库一致的原始数据。API 为公开只读访问，无需登录。

主要端点：

| 端点 | 说明 |
| :--- | :--- |
| `GET /articles?ns=0&page=1&size=100` | 文章索引（分页，返回 `total`） |
| `GET /articles/by-title?ns=0&title=…&withRevision=1` | 文章 + 当前修订（核心数据） |
| `GET /search?q=…` | 搜索 |

文章正文 `revision.contentJson` 是 ProseMirror 结构化 JSON：普通文章为标题/段落/
列表/表格等文本节点；干员、武器等条目为 `wikiTemplateInstance`（模板实例）与
`endfieldCard*` 游戏数据卡片节点。`revision.contentText` 是 wiki 数据库自带的
纯文本视图。这些就是 wiki 的核心数据，全部原样保留。

## 使用方法

工具脚本可以放在任意位置（移动不影响使用）；**数据输出目录每次运行时指定**：

```bash
# 全量/增量同步主命名空间（默认）
python sync.py /path/to/data_dir

# 附带模板与分类命名空间
python sync.py /path/to/data_dir --templates --categories

# 只查看差异，不下载
python sync.py /path/to/data_dir --check-only

# 输出 JSON 与 Markdown 两种格式
python sync.py /path/to/data_dir --export both

# 忽略本地状态，强制全量重下
python sync.py /path/to/data_dir --full
```

常用参数：

| 参数 | 说明 | 默认 |
| :--- | :--- | :--- |
| `output_dir` | 数据输出目录（必填，运行时指定） | — |
| `--ns 0 10 14` | 要同步的命名空间 | `0`（主） |
| `--templates` / `--categories` | 快捷追加命名空间 10 / 14 | 关 |
| `--prefix 干员/` | 只同步标题前缀匹配的文章 | 全部 |
| `--full` | 忽略本地状态全量重下 | 关（增量） |
| `--check-only` | 仅输出差异清单 | 关 |
| `--export json\|md\|both` | 导出格式 | `json` |
| `--jobs N` | 并发请求数 | 4 |
| `--delay S` | 请求最小间隔（秒），避免给源站造成压力 | 0.12 |
| `--no-prune` | 不删除远端已不存在的本地文件 | 会清理 |

## 输出结构（镜像 wiki 数据库排布）

```
OUTPUT_DIR/
  meta/
    sync_state.json    增量状态（每篇文章的 updatedAt / 修订，用于增量同步）
    last_run.json      最近一次运行摘要（含失败明细）
  wiki/
    index.json         文章索引（articles 表摘要：ns/title/id/categories/updatedAt）
    articles/
      干员/佩丽卡.json        ← 文章 + 当前修订的原始 API 载荷
      干员/佩丽卡.md          ← 便捷视图（见下）
      Template/干员卡.json    ← 命名空间 10（--templates）
      Category/地区.json      ← 命名空间 14（--categories）
```

- **`.json` 是权威数据**：`{ "article": {…}, "revision": {…} }`，
  与 `GET /articles/by-title?…&withRevision=1` 返回的原始载荷完全一致。
  文章标题的 `/` 层级映射为目录层级，与 wiki 页面 URL（`/wiki/干员/佩丽卡`）对应。
- **`.md` 仅是便捷视图**：优先输出 wiki 数据库自带的 `contentText` 纯文本；
  正文为结构化 JSON 的条目会注明"详见同名 .json 文件"。不要依赖 md 的排版，
  需要完整数据一律读 `.json`。

## 增量更新原理

1. 分页拉取命名空间索引（每页 100 条，共 36 页左右），得到每篇文章的
   `updatedAt`；
2. 与 `meta/sync_state.json` 对比：新增 → 下载；`updatedAt` 变化 → 重新下载；
   未变 → 跳过；远端已删除 → 清理本地文件；
3. 只重写发生变化的文章，其余保持不动，因此日常更新非常快
   （3600 篇文章中通常只有几篇变化）。

## 注意事项

- 请保持合理的 `--jobs` / `--delay`，不要对源站造成压力（默认值已经比较保守）。
- 目录只放本工具产出；`--prune` 只会删除**之前由本工具同步过**且远端已消失的
  文件，不会碰其他文件。
- 本工具只同步 wiki 的核心数据（文章正文与元数据）。导航配置、公告、用户、
  积分、审核等框架性内容不属于"核心数据"，不在此列。

---

# prepare_rag.py —— 整理为 RAG 源数据

把 `sync.py` 同步得到的**原始 JSON**（不动原数据）进一步整理为适合作为
RAG（检索增强生成）语料的文本，产物**另存**到独立输出目录。

## 用法

```bash
# 输入：sync.py 的输出目录；不指定 -o 时默认输出到 <输入目录>_rag
python prepare_rag.py /path/to/data_dir

# 自定义输出路径 + 生成检索块
python prepare_rag.py /path/to/data_dir -o /path/to/rag_data --chunks

# 忽略增量状态，全部重新生成
python prepare_rag.py /path/to/data_dir --force
```

| 参数 | 说明 | 默认 |
| :--- | :--- | :--- |
| `input_dir` | sync.py 的输出目录（含 `wiki/index.json`） | 必填 |
| `-o / --output` | RAG 输出目录（可选） | `<输入目录>_rag` |
| `--chunks` | 额外生成按标题切分的检索块 `chunks.jsonl` | 关 |
| `--force` | 忽略增量状态全量重生成 | 关（增量） |

## 输出结构

```
OUTPUT_DIR/
  documents/干员/佩丽卡.md   每篇文章一份结构化 Markdown
  index.json                文档清单（标题/分类/更新时间/字符数/来源链接）
  rag_state.json            增量状态（按 updatedAt 跳过未变化的文章）
  chunks.jsonl              （--chunks）按标题切分的检索块，每行一个 JSON
```

每份文档以 YAML front matter 开头携带元数据（标题、命名空间、文章 ID、
分类、更新时间、原文 URL），正文为整理后的可读文本：

- ProseMirror 标题/段落/列表/表格 → Markdown，`wikiLink` 保留为站内链接；
- 游戏数据模板（`wikiTemplateInstance`）与数据卡片（`endfieldCard*`）→
  扁平化为键值行、表格与分级小节（技能等级 `Lv1：…`、属性曲线表、
  索引表等）；
- 游戏内 `<@tag>…</>` 占位标记被清洗，仅保留可读文字；
- 异构复杂结构（如图标资源对象）以单行 JSON 保留，不丢数据。

## 推荐流程

```bash
# 1) 同步（增量，一般只抓变化文章）
python sync.py data_dir
# 2) 整理为 RAG 语料（增量，只重生成变化的文档）
python prepare_rag.py data_dir --chunks
# 3) 将 documents/ 或 chunks.jsonl 交给向量化/切分工具即可
```

`chunks.jsonl` 每行包含：`id`、`article`、`section`（标题路径）、`text`、
`characters`、`namespace`、`updated_at`、`source`、`file`，可直接按行读入
向量库，或继续按自己的块大小二次切分。
