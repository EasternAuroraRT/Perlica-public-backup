#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fz_wiki_sync —— 终末地 Wiki（https://fz.wiki）数据同步工具
================================================================

数据来源
--------
fz.wiki 是一个 Next.js 动态站点，页面内容由前端通过后端 API 动态加载，
直接抓取 HTML 拿不到有效数据。前端 JS 中暴露了 API 基础地址
`https://api.fz.wiki`，所有数据请求均形如：

    GET {API_BASE}/api/v1/...

本工具直接调用该 API，获取与 wiki 数据库一致的原始数据：

  * 文章列表（articles 表摘要）：GET /articles?ns=..&page=..&size=100
  * 文章 + 当前修订（articles JOIN revisions）：
        GET /articles/by-title?ns=..&title=..&withRevision=1

文章的正文是 ProseMirror 结构化 JSON（revision.contentJson），
包含文本段落、表格以及 wiki 特有的模板实例（wikiTemplateInstance）与
游戏数据卡片（endfieldCard* 等）——这就是 wiki 的核心数据。

输出结构（镜像 wiki 标题层级）
------------------------------
OUTPUT_DIR/
  meta/
    sync_state.json    增量同步状态（记录每篇文章的 updatedAt / 修订 id）
    last_run.json      最近一次运行摘要
  wiki/
    index.json         全部文章索引（articles 表镜像）
    articles/          每篇文章一个文件，目录层级 = 标题层级
      干员/佩丽卡.json         （ns=0 主命名空间，无前缀）
      Template/干员卡.json     （ns=10 模板，前缀 Template）
      Category/地区.json       （ns=14 分类，前缀 Category）

仅同步 wiki 的核心数据（文章正文与元数据），不含导航配置、公告、
用户、积分、审核等框架性内容。

运行方式
--------
工具脚本本身可以放在任何位置；每次运行时通过命令行参数指定数据输出目录：

    python sync.py /path/to/output_dir

详见 README.md。
"""

import argparse
import json
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_API_BASE = "https://api.fz.wiki"
API_PREFIX = "/api/v1"

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36 fz-wiki-sync/1.0"

# 与前端 JS 中 Namespace 枚举一致
NAMESPACE_NAMES = {
    0: "", 1: "Talk", 2: "User", 3: "User_talk",
    4: "Project", 5: "Project_talk", 6: "File", 7: "File_talk",
    8: "MediaWiki", 9: "MediaWiki_talk", 10: "Template", 11: "Template_talk",
    12: "Help", 13: "Help_talk", 14: "Category", 15: "Category_talk",
    828: "Module", -1: "Special",
}
NS_MAIN = 0
NS_TEMPLATE = 10
NS_CATEGORY = 14

STATE_VERSION = 1

# ---------------------------------------------------------------- HTTP 层


def http_get_json(url, timeout=30, retries=3, base_delay=1.0, quiet=False):
    """GET 并解析 JSON；失败按指数退避重试。"""
    last_err = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, headers={
            "User-Agent": UA,
            "Accept": "application/json",
            "Accept-Language": "zh-CN,zh;q=0.9",
        })
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code in (401, 403, 404):
                raise  # 无意义重试
            if not quiet:
                print(f"    ! HTTP {e.code} {url}", file=sys.stderr)
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            last_err = e
            if not quiet:
                print(f"    ! 请求失败: {e}", file=sys.stderr)
        if attempt < retries:
            time.sleep(base_delay * (2 ** attempt))
    if last_err is None:
        raise RuntimeError(f"请求失败（无异常细节）: {url}")
    raise last_err


def api_get(base, path, params=None, **kw):
    url = base.rstrip("/") + API_PREFIX + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    return http_get_json(url, **kw)


# ------------------------------------------------------------ 索引与抓取


def list_namespace(base, ns, timeout=30, retries=3, quiet=False):
    """分页拉取某个命名空间的全部文章摘要。"""
    articles = []
    page = 1
    total = None
    while True:
        d = api_get(base, "/articles", {"ns": ns, "page": page, "size": 100},
                    timeout=timeout, retries=retries, quiet=quiet)
        batch = d.get("articles") or []
        articles.extend(batch)
        total = d.get("total", total)
        if not batch or len(articles) >= (total or 0):
            break
        page += 1
    return articles


def fetch_article(base, ns, title, **kw):
    """按标题取文章 + 当前修订（原始 API 载荷）。"""
    return api_get(base, "/articles/by-title",
                   {"ns": ns, "title": title, "withRevision": 1}, **kw)


# ------------------------------------------------------------ 文件布局


_INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"CON", "PRN", "AUX", "NUL",
             *(f"COM{i}" for i in range(1, 10)),
             *(f"LPT{i}" for i in range(1, 10))}


def sanitize_component(name):
    """把标题单段变成安全文件/目录名。"""
    name = _INVALID_CHARS.sub("_", name).strip().strip(".")
    if not name:
        name = "_"
    if name.upper() in _RESERVED:
        name = "_" + name
    return name


def article_relpath(ns, title, ext):
    """文章标题 -> 相对路径（目录层级 = 标题的 '/' 层级）。

    注意：不能使用 Path.with_suffix()——标题末尾常含点号段（如
    “O.B.J.尖峰”），with_suffix 会把最后一个点号段当成扩展名截掉，
    导致不同标题互相覆盖。这里显式拼接扩展名。
    """
    prefix = NAMESPACE_NAMES.get(ns, f"ns{ns}")
    parts = ([prefix] if prefix else []) + title.split("/")
    safe = [sanitize_component(p) for p in parts]
    safe[-1] = safe[-1] + ext
    return Path(*safe)


def state_key(ns, title):
    return f"{ns}:{title}"


# ------------------------------------------------------------ 增量状态


def load_state(output_dir):
    p = Path(output_dir) / "meta" / "sync_state.json"
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"version": STATE_VERSION, "api_base": DEFAULT_API_BASE,
            "synced_at": None, "articles": {}}


def save_state(output_dir, state):
    p = Path(output_dir) / "meta" / "sync_state.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(p, json.dumps(state, ensure_ascii=False, indent=2))


def save_last_run(output_dir, summary):
    p = Path(output_dir) / "meta" / "last_run.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(p, json.dumps(summary, ensure_ascii=False, indent=2))


def _atomic_write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


# ------------------------------------------------------------ 差异规划


def plan_sync(index_by_key, state_articles, prefix=None):
    """返回 (新建, 更新, 跳过, 移除) 四个列表。"""
    new, updated, skipped, removed = [], [], [], []
    def in_scope(art):
        return prefix is None or art.get("title", "").startswith(prefix)

    for key, art in index_by_key.items():
        if not in_scope(art):
            continue
        old = state_articles.get(key)
        if old is None:
            new.append(art)
        elif old.get("updatedAt") != art.get("updatedAt"):
            updated.append(art)
        else:
            skipped.append(art)
    for key, old in state_articles.items():
        if key not in index_by_key and in_scope(old):
            removed.append(old)
    return new, updated, skipped, removed


# ------------------------------------------------------------ 下载


def sync_articles(base, arts, output_dir, state_articles, jobs, delay,
                  timeout, retries, export_modes, quiet):
    """并发下载文章并写盘。返回统计与失败列表。"""
    lock = threading.Lock()
    stats = {"fetched": 0, "saved_json": 0, "saved_md": 0, "failed": 0}
    failures = []
    last_req = [0.0]

    def worker(art):
        # 简单的全局节流
        with lock:
            wait = delay - (time.time() - last_req[0])
            if wait > 0:
                time.sleep(wait)
            last_req[0] = time.time()
        ns = art.get("namespace", 0)
        try:
            payload = fetch_article(base, ns, art["title"],
                                    timeout=timeout, retries=retries, quiet=quiet)
        except Exception as e:
            return art, None, e

        article = payload.get("article") or {}
        revision = payload.get("revision")
        title = article.get("title") or art["title"]
        rel = article_relpath(ns, title, ".json")
        if not quiet:
            print(f"  [{'N' if art['key'] not in state_articles else 'U'}] "
                  f"{ns}:{title}")
        saved = {"json": False, "md": False}
        with lock:
            if "json" in export_modes:
                _atomic_write(output_dir / "wiki" / "articles" / rel,
                              json.dumps(payload, ensure_ascii=False, indent=2))
                saved["json"] = True
            if "md" in export_modes:
                md = render_article_markdown(payload)
                _atomic_write(output_dir / "wiki" / "articles" /
                              rel.with_suffix(".md"), md)
                saved["md"] = True
        return art, saved, None

    with ThreadPoolExecutor(max_workers=max(1, jobs)) as ex:
        futs = {ex.submit(worker, a): a for a in arts}
        for fut in as_completed(futs):
            art, saved, err = None, {}, None
            try:
                art, saved, err = fut.result()
            except Exception as e:  # 防御：worker 内意外异常也计入失败
                art = futs[fut]
                err = e
            with lock:
                if err is not None:
                    stats["failed"] += 1
                    failures.append((art, str(err)))
                else:
                    stats["fetched"] += 1
                    stats["saved_json"] += 1 if (saved or {}).get("json") else 0
                    stats["saved_md"] += 1 if (saved or {}).get("md") else 0
    return stats, failures


# ------------------------------------------------------------ Markdown 渲染
#
# 主输出是 API 原始 JSON（articles + revisions 数据）。Markdown 仅作为
# 便捷视图：优先使用 wiki 数据库 revisions 表自带的 contentText（原始纯文本），
# 模板实例类文章（正文为结构化 contentJson）则给出指向 JSON 文件的提示。

def render_article_markdown(payload):
    article = payload.get("article") or {}
    revision = payload.get("revision") or {}
    title = article.get("title", "")
    lines = [f"# {title}", ""]
    text = (revision.get("contentText") or "").strip()
    desc = (article.get("description") or "").strip()
    # contentText 通常已包含 description，避免重复引用
    if desc and not text.startswith(desc):
        lines += [f"> {desc}", ""]
    cj = revision.get("contentJson") or {}
    cj_content = cj.get("content") or []
    if text and len(text) >= 40:
        lines.append(text)
    elif cj_content:
        lines.append("> 正文为结构化游戏数据（ProseMirror JSON），详见同名 .json 文件。")
    elif text:
        lines.append(text)
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- 主流程


def parse_args(argv):
    ap = argparse.ArgumentParser(
        prog="sync.py",
        description="终末地 Wiki (fz.wiki) 数据同步工具 —— 通过官方后端 API 抓取原始数据",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__)
    ap.add_argument("output_dir", help="数据输出目录（每次运行时指定，脚本本身可放在任意位置）")
    ap.add_argument("--api-base", default=DEFAULT_API_BASE,
                    help=f"API 基础地址（默认 {DEFAULT_API_BASE}）")
    ap.add_argument("--ns", type=int, nargs="+", default=[NS_MAIN],
                    help="要同步的命名空间（默认 0=主命名空间）")
    ap.add_argument("--templates", action="store_true", help="额外同步模板命名空间 (10)")
    ap.add_argument("--categories", action="store_true", help="额外同步分类命名空间 (14)")
    ap.add_argument("--prefix", default=None,
                    help="仅同步标题以该前缀开头的文章（如 干员/）")
    ap.add_argument("--full", action="store_true",
                    help="忽略本地状态，全部重新下载")
    ap.add_argument("--check-only", action="store_true",
                    help="只输出差异（新增/更新/移除），不下载")
    ap.add_argument("--export", choices=["json", "md", "both"], default="json",
                    help="导出格式：原始 JSON / Markdown / 两者（默认 json）")
    ap.add_argument("--jobs", type=int, default=4, help="并发请求数（默认 4）")
    ap.add_argument("--delay", type=float, default=0.12,
                    help="每次请求之间的最小间隔秒数（默认 0.12）")
    ap.add_argument("--timeout", type=float, default=30, help="请求超时秒数（默认 30）")
    ap.add_argument("--retries", type=int, default=3, help="请求失败重试次数（默认 3）")
    ap.add_argument("--no-prune", action="store_true",
                    help="不删除远端已不存在的本地文件（默认会清理）")
    ap.add_argument("--quiet", action="store_true", help="减少输出")
    return ap.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    export_modes = {"json", "md"} if args.export == "both" else {args.export}
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    nss = list(args.ns)
    if args.templates and NS_TEMPLATE not in nss:
        nss.append(NS_TEMPLATE)
    if args.categories and NS_CATEGORY not in nss:
        nss.append(NS_CATEGORY)

    state = load_state(output_dir)
    if args.api_base != DEFAULT_API_BASE or state.get("api_base") != args.api_base:
        state["api_base"] = args.api_base
    state_articles = state.setdefault("articles", {})

    started = datetime.now(timezone.utc)
    print(f"== 终末地 Wiki 同步 ==")
    print(f"   API: {args.api_base}")
    print(f"   输出: {output_dir}")
    print(f"   命名空间: {nss}")

    # 1) 索引
    index_by_key = {}
    for ns in nss:
        print(f"== 拉取命名空间 {ns} ({NAMESPACE_NAMES.get(ns, '?')}) 索引 ==")
        arts = list_namespace(args.api_base, ns, timeout=args.timeout,
                              retries=args.retries, quiet=args.quiet)
        for a in arts:
            key = state_key(ns, a["title"])
            a["key"] = key
            index_by_key[key] = a
        if not args.quiet:
            print(f"   {len(arts)} 篇文章")

    # 2) 差异
    new, updated, skipped, removed = plan_sync(index_by_key, state_articles,
                                               prefix=args.prefix)
    if args.full:
        new = [a for a in index_by_key.values()
               if args.prefix is None or a["title"].startswith(args.prefix)]
        updated, skipped = [], []
    print(f"== 差异：新增 {len(new)}，更新 {len(updated)}，无变化 {len(skipped)}，"
          f"远端已删除 {len(removed)} ==")
    if not args.quiet:
        for label, lst in (("新增", new), ("更新", updated), ("远端删除", removed)):
            if lst:
                shown = [a["title"] for a in lst[:50]]
                print(f"   {label}（前 {len(shown)}/共 {len(lst)}）: " + "、".join(shown))

    # 3) 索引文件
    index_out = []
    for a in sorted(index_by_key.values(), key=lambda x: x["title"]):
        index_out.append({
            "namespace": a.get("namespace"),
            "title": a["title"],
            "id": a.get("id"),
            "categories": a.get("categories", []),
            "updatedAt": a.get("updatedAt"),
            "file": str(article_relpath(a.get("namespace", 0), a["title"], ".json")),
        })
    _atomic_write(output_dir / "wiki" / "index.json",
                  json.dumps(index_out, ensure_ascii=False, indent=2))

    # 4) 清理远端已删除
    pruned = 0
    if removed and not args.no_prune and not args.check_only:
        for old in removed:
            old_key = old.get("key") or state_key(old.get("ns", 0), old.get("title", ""))
            rel = article_relpath(old.get("ns", 0), old.get("title", ""), ".json")
            for ext in (".json", ".md"):
                p = output_dir / "wiki" / "articles" / rel.with_suffix(ext)
                if p.exists():
                    p.unlink()
                    pruned += 1
            state_articles.pop(old_key, None)
        print(f"   已清理 {pruned} 个本地文件（远端已删除）")

    # 5) 下载
    todo = new + updated
    stats = {"fetched": 0, "saved_json": 0, "saved_md": 0, "failed": 0}
    failures = []
    if todo and not args.check_only:
        stats, failures = sync_articles(
            args.api_base, todo, output_dir, state_articles,
            args.jobs, args.delay, args.timeout, args.retries,
            export_modes, args.quiet)
        # 更新状态（失败的条目不标记为已同步，下次运行会重试）
        failed_keys = {art["key"] for art, _ in failures}
        for a in todo:
            if a["key"] in failed_keys:
                continue
            key = a["key"]
            f = Path("wiki") / "articles" / article_relpath(
                a.get("namespace", 0), a["title"], ".json")
            state_articles[key] = {
                "ns": a.get("namespace", 0),
                "title": a["title"],
                "id": a.get("id"),
                "updatedAt": a.get("updatedAt"),
                "file": str(f),
            }

    # 6) 保存状态与摘要（check-only 不改动同步状态）
    state["synced_at"] = datetime.now(timezone.utc).isoformat()
    if not args.check_only:
        save_state(output_dir, state)
    summary = {
        "synced_at": state["synced_at"],
        "started_at": started.isoformat(),
        "api_base": args.api_base,
        "namespaces": nss,
        "counts": {
            "indexed": len(index_by_key),
            "new": len(new),
            "updated": len(updated),
            "skipped": len(skipped),
            "removed": len(removed),
            "pruned_files": pruned,
            "fetched": stats["fetched"],
            "saved_json": stats["saved_json"],
            "saved_md": stats["saved_md"],
            "failed": stats["failed"],
        },
    }
    if failures:
        summary["failures"] = [{"title": art["title"], "error": e}
                               for art, e in failures]
    save_last_run(output_dir, summary)

    print(f"== 完成：抓取 {stats['fetched']}，失败 {stats['failed']}，"
          f"索引 {len(index_by_key)} 篇 ==")
    for art, e in failures[:20]:
        print(f"   FAIL {art['title']}: {e}", file=sys.stderr)
    if failures:
        print(f"   （共 {len(failures)} 个失败，详见 meta/last_run.json）",
              file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
