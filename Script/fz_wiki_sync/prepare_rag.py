#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
prepare_rag —— 将 fz_wiki_sync 同步得到的 wiki 原始数据整理为 RAG 源数据
========================================================================

输入：fz_wiki_sync 的输出目录（含 wiki/index.json 与 wiki/articles/**/*.json）。
原始 JSON 数据不做任何改动；本工具只读取，产物另存到独立的输出目录。

输出：
    OUTPUT_DIR/
      documents/<标题层级>/<标题>.md   每篇文章一份结构化 Markdown
      index.json                      全部文档的清单（含元数据/字符数）
      rag_state.json                  增量状态（记录每篇 updatedAt）
      chunks.jsonl                    可选（--chunks）：按标题切分的检索块

每份 Markdown 以 YAML front matter 开头，携带标题、命名空间、分类、
更新时间、wiki 原文链接等元数据，正文为可读的结构化文本：
ProseMirror 的标题/段落/列表/表格被转为 Markdown；游戏数据模板
（wikiTemplateInstance）与数据卡片（endfieldCard*）被扁平化为
键值行、表格与分级小节；游戏内 <@tag>…</> 标记被清洗。

用法：
    python prepare_rag.py <sync输出目录> [-o <输出目录>] [--chunks] [--force]
    # 不指定 -o 时，输出到 <sync输出目录> 的同级 <目录名>_rag/
"""

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from .sync import article_relpath, NAMESPACE_NAMES, _atomic_write
except ImportError:  # 独立使用时的兜底（不应发生）
    NAMESPACE_NAMES = {0: "", 10: "Template", 14: "Category"}

    _INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
    _RESERVED = {"CON", "PRN", "AUX", "NUL",
                 *(f"COM{i}" for i in range(1, 10)),
                 *(f"LPT{i}" for i in range(1, 10))}

    def sanitize_component(name):
        name = _INVALID_CHARS.sub("_", name).strip().strip(".")
        if not name:
            name = "_"
        if name.upper() in _RESERVED:
            name = "_" + name
        return name

    def article_relpath(ns, title, ext):
        prefix = NAMESPACE_NAMES.get(ns, f"ns{ns}")
        parts = ([prefix] if prefix else []) + title.split("/")
        safe = [sanitize_component(p) for p in parts]
        safe[-1] = safe[-1] + ext
        return Path(*safe)

    def _atomic_write(path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)


# ------------------------------------------------------------ 文本清洗


_MARKUP_OPEN = re.compile(r"<[@#&$][\w.]*>")       # <@ba.key> <#ba.conduct> 等游戏标记
_MARKUP_CLOSE = re.compile(r"</>")


def clean_markup(s):
    """清洗游戏数据文本中的 <@tag>…</> 占位标记，保留内部文字。"""
    if not isinstance(s, str):
        return str(s)
    s = _MARKUP_OPEN.sub("", s)
    s = _MARKUP_CLOSE.sub("", s)
    return s


# ------------------------------------------------------------ 值渲染


def _is_scalar(v):
    return v is None or isinstance(v, (str, int, float, bool))


def _json_block(v):
    return ["```json", json.dumps(v, ensure_ascii=False, indent=2), "```"]


def _meta_table(items):
    out = ["| 项目 | 内容 |", "| :--- | :--- |"]
    for x in items:
        out.append(f"| {x.get('label', '')} | {clean_markup(str(x.get('value', '')))} |")
    return out


def _record_table(records):
    cols = list(records[0].keys())
    out = ["| " + " | ".join(cols) + " |",
           "| " + " | ".join("---" for _ in cols) + " |"]
    for r in records:
        out.append("| " + " | ".join(str(r.get(c, "")) for c in cols) + " |")
    return out


def _skill_levels(items):
    return [f"- Lv{x.get('level', '?')}：{clean_markup(str(x.get('desc', '')))}"
            for x in items]


def render_value(val, out, depth=0, style="heading"):
    """把模板字段值扁平化为可读文本行；深度过深或太异构的结构回退 JSON 块。
    style="bold" 表示当前处于列表项内，嵌套小节用加粗而非标题。"""
    if depth > 4:
        out.extend(_json_block(val))
        return
    if val is None or val == "":
        return
    if isinstance(val, str):
        out.append(clean_markup(val))
        return
    if _is_scalar(val):
        out.append(str(val))
        return
    if isinstance(val, list):
        if not val:
            return
        if all(_is_scalar(x) for x in val):
            out.append("、".join(str(x) for x in val))
            return
        if all(isinstance(x, dict) for x in val):
            # meta 表（label/value）
            if (all({"label", "value"} <= set(x) for x in val)
                    and all(set(x) <= {"label", "value"} for x in val)):
                out.extend(_meta_table(val))
                return
            # 技能等级
            if all("level" in x and "desc" in x for x in val):
                out.extend(_skill_levels(val))
                return
            # 统一键的记录表（如敌人属性曲线）
            keys = set(val[0].keys())
            if (keys and len(keys) <= 8
                    and all(set(x.keys()) == keys for x in val)
                    and all(_is_scalar(x[k]) for x in val for k in keys)):
                out.extend(_record_table(val))
                return
            # 带 name 的对象列表 -> 逐项小节
            if all("name" in x for x in val):
                for x in val:
                    name = clean_markup(str(x.get("name", "")))
                    out.append(f"- **{name}**")
                    for k, v in x.items():
                        if k == "name":
                            continue
                        if (k == "levels" and isinstance(v, list) and v
                                and all(isinstance(l, dict) and "level" in l
                                        and "desc" in l for l in v)):
                            out.extend(_skill_levels(v))
                            continue
                        if _is_scalar(v) and v not in (None, ""):
                            out.append(f"  - {k}: {clean_markup(str(v))}")
                        elif isinstance(v, dict) and all(_is_scalar(xx) for xx in v.values()):
                            out.append("  - " + k + ": " + "、".join(
                                f"{kk}={vv}" for kk, vv in v.items()
                                if vv not in (None, "")))
                        else:
                            # 异构复杂值（如图标资源对象）单行 JSON，保持无损且不破坏列表结构
                            out.append(f"  - {k}: {json.dumps(v, ensure_ascii=False)}")
                return
        out.extend(_json_block(val))
        return
    if isinstance(val, dict):
        if not val:
            return
        # 单键包装结构（如 {skills: [...]}）直接展开，避免重复标题
        if len(val) == 1:
            k, v = next(iter(val.items()))
            if v is not None and v != "":
                render_value(v, out, depth + 1)
            return
        if "meta" in val and isinstance(val["meta"], list) and val["meta"]:
            out.extend(_meta_table(val["meta"]))
            for k, v in val.items():
                if k == "meta":
                    continue
                _render_keyed(k, v, out, depth + 1, style=style)
            return
        if all(_is_scalar(v) for v in val.values()):
            for k, v in val.items():
                if v is None or v == "":
                    continue
                out.append(f"- {k}：{clean_markup(str(v))}")
            return
        for k, v in val.items():
            if v is None or v == "":
                continue
            _render_keyed(k, v, out, depth + 1, style=style)
        return
    out.extend(_json_block(val))


def _render_keyed(key, val, out, depth, style="heading"):
    """渲染一个命名值。style=heading 用 ###，style=bold 用 **key**（列表内）。"""
    if _is_scalar(val):
        if val not in (None, ""):
            out.append(f"- {key}：{clean_markup(str(val))}")
        return
    if style == "heading":
        out.append(f"### {key}")
    else:
        out.append(f"**{key}**")
    render_value(val, out, depth, style=style)
    out.append("")


# ------------------------------------------------------------ 节点渲染


def _inline_text(node):
    txt = node.get("text", "")
    if not txt:
        return ""
    for m in node.get("marks") or []:
        mt = m.get("type")
        if mt == "bold":
            txt = f"**{txt}**"
        elif mt == "italic":
            txt = f"*{txt}*"
        elif mt == "wikiLink":
            href = m.get("attrs", {}).get("href") or ""
            txt = f"[{txt}](/wiki/{href.lstrip('/')})"
        elif mt in ("textStyle", "link"):
            href = m.get("attrs", {}).get("href")
            if href:
                txt = f"[{txt}]({href})"
    return txt


def _inline(nodes):
    return "".join(_inline_text(n) for n in nodes or [])


def _render_node(node, out, depth=0):
    t = node.get("type")
    if t == "heading":
        lv = min(max(int(node.get("attrs", {}).get("level", 1)), 1), 6)
        out.append("#" * lv + " " + _inline(node.get("content", [])))
    elif t == "paragraph":
        txt = _inline(node.get("content", []))
        if txt:
            out.append(txt)
    elif t == "text":
        txt = _inline_text(node)
        if txt:
            out.append(txt)
    elif t == "bulletList":
        for li in node.get("content", []):
            if li.get("type") != "listItem":
                continue
            txt = _inline(li.get("content", []))
            if txt:
                out.append("- " + txt)
    elif t == "orderedList":
        for i, li in enumerate(node.get("content", []), 1):
            txt = _inline(li.get("content", []))
            if txt:
                out.append(f"{i}. {txt}")
    elif t == "blockquote":
        txt = _inline(node.get("content", [])).strip()
        if txt:
            for line in txt.split("\n"):
                out.append(f"> {line}")
    elif t == "codeBlock":
        out.extend(["```", node.get("text", ""), "```"])
    elif t == "horizontalRule":
        out.append("---")
    elif t == "hardBreak":
        out.append("")
    elif t == "image":
        src = node.get("attrs", {}).get("src", "")
        alt = node.get("attrs", {}).get("alt", "")
        out.append(f"![{alt}]({src})")
    elif t == "table":
        _render_table(node, out)
    elif t == "wikiTemplateInstance":
        _render_template(node.get("attrs"), out)
    elif t == "wikiIndexTable":
        _render_index_table(node.get("attrs"), out)
    elif t == "tabs":
        for tab in node.get("content") or []:
            if tab.get("type") != "tabsTab":
                continue
            label = tab.get("attrs", {}).get("label") or ""
            if label:
                out.append(f"### {clean_markup(str(label))}")
            _render_children(tab.get("content", []), out, depth + 1)
    elif t == "details":
        for c in node.get("content") or []:
            if c.get("type") == "detailsSummary":
                out.append("**" + _inline(c.get("content", [])) + "**")
            else:
                _render_children(c.get("content", []), out, depth + 1)
    elif t == "detailsContent":
        _render_children(node.get("content", []), out, depth + 1)
    elif t in ("Level0", "Level1"):
        _render_children(node.get("content", []), out, depth + 1)
    elif t.startswith("endfieldCard"):
        attrs = node.get("attrs") or {}
        name = attrs.get("name") if isinstance(attrs.get("name"), str) else None
        out.append(f"### {clean_markup(name) if name else t}")
        render_value(attrs, out, 1)
        out.append("")
        _render_children(node.get("content", []), out, depth + 1)
    else:  # 未知节点：attrs + 子节点兜底
        attrs = node.get("attrs")
        if attrs:
            out.append(f"### {t}")
            render_value(attrs, out, 1)
            out.append("")
        _render_children(node.get("content", []), out, depth + 1)


def _render_children(nodes, out, depth=0):
    for n in nodes or []:
        _render_node(n, out, depth)


def _render_table(node, out):
    rows = []
    for row in node.get("content") or []:
        if row.get("type") != "tableRow":
            continue
        cells = [_inline(c.get("content", [])).strip()
                 for c in row.get("content") or []
                 if c.get("type") in ("tableCell", "tableHeader")]
        rows.append(cells)
    if not rows:
        return
    out.append("| " + " | ".join(rows[0]) + " |")
    out.append("| " + " | ".join("---" for _ in rows[0]) + " |")
    for r in rows[1:]:
        out.append("| " + " | ".join(r) + " |")
    out.append("")


def _render_template(attrs, out):
    if not isinstance(attrs, dict):
        return
    name = attrs.get("templateName")
    if name:
        out.append(f"## 模板：{name}")
    for key, val in attrs.items():
        if key == "templateName":
            continue
        _render_keyed(key, val, out, 1)


def _render_index_table(attrs, out):
    if not isinstance(attrs, dict):
        return
    cols = attrs.get("columns") or []
    entries = attrs.get("entries") or []
    if not cols:
        return
    visible = [c for c in cols if not c.get("hidden")]
    out.append("| " + " | ".join(c.get("label", c.get("key", "")) for c in visible) + " |")
    out.append("| " + " | ".join("---" for _ in visible) + " |")
    for e in entries:
        cells = []
        for c in visible:
            v = e.get(c.get("key"))
            if c.get("kind") == "link" and c.get("titleField"):
                tgt = e.get(c.get("titleField")) or v
                cells.append(f"[{v}](/wiki/{tgt})" if v else "")
            else:
                cells.append(str(v) if v is not None else "")
        out.append("| " + " | ".join(cells) + " |")
    empty = attrs.get("emptyHint")
    if empty and not entries:
        out.append(f"*{empty}*")
    out.append("")


# ------------------------------------------------------------ 文章渲染


def render_article(payload):
    """文章 -> (front matter dict, markdown 正文)。"""
    article = payload.get("article") or {}
    revision = payload.get("revision") or {}
    title = article.get("title", "")
    ns = article.get("namespace", 0)

    meta = {
        "title": title,
        "namespace": ns,
        "article_id": article.get("id"),
        "categories": article.get("categories", []),
        "updated_at": article.get("updatedAt"),
        "source": f"https://www.fz.wiki/wiki/{title}",
    }

    lines = [f"# {title}", ""]
    desc = (article.get("description") or "").strip()
    cj = revision.get("contentJson") or {}
    text = (revision.get("contentText") or "").strip()
    cj_content = cj.get("content") or []

    def norm(s):  # 折叠空白，用于判断正文是否已含描述
        return re.sub(r"\s+", " ", s).strip()

    if desc and not norm(text or "").startswith(norm(desc)[:40]):
        lines += [f"> {desc}", ""]
    had_body = False
    if cj_content:
        before = len(lines)
        _render_children(cj_content, lines)
        had_body = any(l.strip() for l in lines[before:])
        if not had_body:  # 只有标题没有正文
            lines.append(f"> 正文为空或仅为结构化数据，见原文：{meta['source']}")
    elif text:
        lines.append(text)
        had_body = True
    else:
        lines.append("> 该条目当前没有正文内容。")
    body = join_body(lines) + "\n"
    return meta, body


def front_matter(meta):
    cats = "\n".join(f"- {c}" for c in meta["categories"]) or "[]"
    lines = ["---",
             f"title: {meta['title']}",
             f"namespace: {meta['namespace']}",
             f"article_id: {meta['article_id']}",
             "categories:",
             cats,
             f"updated_at: {meta['updated_at']}",
             f"source: {meta['source']}",
             "---"]
    return "\n".join(lines) + "\n"


def _needs_blank(prev, line):
    """判断两行之间是否需要空行分隔（表格/列表内部不加空行，标题前后加）。"""
    if not prev or not line:
        return False
    if line.lstrip().startswith("#"):
        return True          # 标题前加空行
    if line.startswith(("|", "- ", "* ", "> ", "```", "1. ", "2. ")):
        return False
    if prev.startswith(("|", "- ", "* ", "> ", "```", "1. ", "2. ")):
        return False
    if prev.lstrip().startswith("#"):
        return True          # 标题后加空行
    if prev.startswith("![") or line.startswith("!["):
        return False
    return True


def join_body(lines):
    """块感知连接：表格/列表行之间不留空行，段落/标题之间留空行。"""
    out = []
    prev = ""
    for line in lines:
        if line.strip() == "":
            continue
        if _needs_blank(prev, line):
            out.append("")
        out.append(line)
        prev = line
    return "\n".join(out)


# ------------------------------------------------------------ 章节切分


def split_sections(title, body, meta):
    """按 Markdown 标题把文档切成检索块。返回 [{section, text}]。"""
    chunks = []
    cur_heading = None
    cur_lines = []
    for line in body.splitlines():
        if re.match(r"^#{1,4} ", line):
            if cur_lines:
                chunks.append((cur_heading, "\n".join(cur_lines).strip()))
            cur_heading = re.sub(r"^#+\s*", "", line).strip()
            cur_lines = [line]
        else:
            cur_lines.append(line)
    if cur_lines:
        chunks.append((cur_heading, "\n".join(cur_lines).strip()))
    out = []
    for heading, text in chunks:
        if not text:
            continue
        section = f"{title}" + (f" / {heading}" if heading else "")
        out.append({
            "section": section,
            "text": text,
            "characters": len(text),
        })
    return out


# ---------------------------------------------------------------- 主流程


def load_index(input_dir):
    p = Path(input_dir) / "wiki" / "index.json"
    if not p.exists():
        sys.exit(f"错误：找不到 {p}\n请先运行 sync.py 同步数据，并传入其输出目录。")
    return json.loads(p.read_text(encoding="utf-8"))


def load_payload(input_dir, file):
    p = Path(input_dir) / "wiki" / "articles" / file
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="prepare_rag.py",
        description="把 fz_wiki_sync 同步的 wiki 原始数据整理为 RAG 源数据（原始数据不动，产物另存）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__)
    ap.add_argument("input_dir", help="fz_wiki_sync 的输出目录（含 wiki/index.json 与 wiki/articles/）")
    ap.add_argument("-o", "--output", default=None,
                    help="RAG 数据输出目录（可选；默认 <输入目录>_rag）")
    ap.add_argument("--chunks", action="store_true",
                    help="额外生成 chunks.jsonl：按标题切分的检索块")
    ap.add_argument("--force", action="store_true",
                    help="忽略增量状态，全部重新生成")
    ap.add_argument("--quiet", action="store_true", help="减少输出")
    args = ap.parse_args(argv)

    input_dir = Path(args.input_dir).resolve()
    output_dir = Path(args.output or (str(input_dir) + "_rag")).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    docs_root = output_dir / "documents"

    index = load_index(input_dir)
    if not args.quiet:
        print(f"== RAG 数据准备 ==")
        print(f"   输入: {input_dir}")
        print(f"   输出: {output_dir}")
        print(f"   文章数: {len(index)}")

    # 增量状态
    state_path = output_dir / "rag_state.json"
    state = {}
    if state_path.exists() and not args.force:
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except Exception:
            state = {}

    todo = []
    for a in index:
        key = f"{a.get('namespace', 0)}:{a['title']}"
        old = state.get(key)
        if old == a.get("updatedAt") and (docs_root / a["file"].replace(".json", ".md")).exists():
            continue
        todo.append(a)
    if not args.quiet:
        print(f"   需要处理: {len(todo)}（跳过 {len(index) - len(todo)} 篇无变化）")

    started = time.time()
    processed = failed = 0
    chunks_by_id = {}
    if args.chunks:
        chunks_path = output_dir / "chunks.jsonl"
        if chunks_path.exists() and not args.force:
            try:
                for line in chunks_path.read_text(encoding="utf-8").splitlines():
                    c = json.loads(line)
                    if c.get("id"):
                        chunks_by_id[c["id"]] = c
            except Exception:
                chunks_by_id = {}
    new_state = dict(state)
    failures = []

    for i, a in enumerate(todo, 1):
        key = f"{a.get('namespace', 0)}:{a['title']}"
        payload = load_payload(input_dir, a["file"])
        if payload is None:
            failures.append((a["title"], "本地 JSON 不存在（请先同步）"))
            failed += 1
            continue
        try:
            meta, body = render_article(payload)
        except Exception as e:
            failures.append((a["title"], f"渲染失败: {e}"))
            failed += 1
            continue
        md_text = front_matter(meta) + "\n" + body
        rel = Path(a["file"]).with_suffix(".md")
        _atomic_write(docs_root / rel, md_text)
        if args.chunks:
            for c in split_sections(meta["title"], body, meta):
                c.update({
                    "id": f"{meta['namespace']}:{meta['title']}::{c['section']}",
                    "article": meta["title"],
                    "namespace": meta["namespace"],
                    "updated_at": meta["updated_at"],
                    "source": meta["source"],
                    "file": str(rel),
                })
                chunks_by_id[c["id"]] = c
        new_state[key] = a.get("updatedAt")
        processed += 1
        if not args.quiet and (i % 200 == 0 or i == len(todo)):
            print(f"   进度 {i}/{len(todo)}，已生成 {processed}，失败 {failed}，"
                  f"耗时 {time.time() - started:.0f}s")

    # 清理：输入中已消失的文章 -> 删除对应文档
    pruned = 0
    index_keys = {f"{a.get('namespace', 0)}:{a['title']}" for a in index}
    for key in list(new_state):
        if key not in index_keys:
            new_state.pop(key, None)
            # 尽力删除文档（标题可还原）
            ns_s, _, title = key.partition(":")
            try:
                rel = article_relpath(int(ns_s), title, ".md")
                p = docs_root / rel
                if p.exists():
                    p.unlink()
                    pruned += 1
            except Exception:
                pass
    if pruned and not args.quiet:
        print(f"   已清理 {pruned} 篇已从 wiki 删除的文档")

    # 输出清单
    manifest = []
    for a in index:
        key = f"{a.get('namespace', 0)}:{a['title']}"
        rel = Path(a["file"]).with_suffix(".md")
        p = docs_root / rel
        manifest.append({
            "title": a["title"],
            "namespace": a.get("namespace"),
            "id": a.get("id"),
            "categories": a.get("categories", []),
            "updatedAt": a.get("updatedAt"),
            "file": str(rel),
            "characters": len(p.read_text(encoding="utf-8")) if p.exists() else 0,
        })
    _atomic_write(output_dir / "index.json",
                  json.dumps(manifest, ensure_ascii=False, indent=2))
    _atomic_write(state_path, json.dumps(new_state, ensure_ascii=False, indent=2))
    if args.chunks:
        for cid in [cid for cid, c in chunks_by_id.items()
                    if f"{c.get('namespace', 0)}:{c.get('article')}" not in index_keys]:
            chunks_by_id.pop(cid, None)
        _atomic_write(output_dir / "chunks.jsonl",
                      "\n".join(json.dumps(c, ensure_ascii=False)
                                for c in chunks_by_id.values()) + "\n")

    print(f"== 完成：生成 {processed} 篇文档，失败 {failed}，"
          f"清理 {pruned}，耗时 {time.time() - started:.0f}s ==")
    for t, e in failures[:20]:
        print(f"   FAIL {t}: {e}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
