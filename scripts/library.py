#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""paper-library —— 论文库的确定性 I/O 工具。

职责边界（重要，不要越界）：
  本脚本只做「确定性、可重复、不需要理解语义」的事情：
    目录脚手架 / 全文抽取 / 索引重建 / 关键词检索。
  「读懂论文并写出特性卡」是语义工作，由 Agent 完成，本脚本不涉及，
  也永远不会自动生成 problem / core_idea / reuse_when 的内容。

真值来源（single source of truth）：
  每篇论文目录下的 `README.md` 的 YAML frontmatter。
  `index.json` 与 `INDEX.md` 都是可再生的派生产物 —— 任何时刻跑一次
  `reindex` 就能从 README 全量重建，因此不存在「索引漂移」问题。
  手工编辑过 README 之后，直接跑 reindex 即可，不需要手工同步索引。

用法：
  python library.py config
  python library.py init
  python library.py scaffold <论文文件> [--id ID] [--title TITLE] [--force]
  python library.py reindex
  python library.py search "关键词..." [--tag T] [--task T] [--json] [--limit N]
  python library.py list

公共参数：--root <论文库根目录>（优先级最高，其余见 resolve_root）
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import shutil
import sys
import unicodedata
from pathlib import Path

# 让中文输出在 Windows 控制台（默认 cp936）下不炸
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

SKILL_DIR = Path(__file__).resolve().parent.parent
CONFIG_FILE = SKILL_DIR / "config.json"
DEFAULT_ROOT = Path(r"E:\parper")

# frontmatter 的规范字段顺序。reindex 会按这个顺序输出，卡片未写的字段留空。
FIELD_ORDER = [
    "id", "title", "authors", "year", "venue", "source",
    "tags", "task", "problem", "core_idea", "key_mechanisms",
    "results", "limitations", "reuse_when", "related",
    "analysis", "status", "created", "updated",
]
# reindex/search 必须依赖的最小字段，缺失会在 reindex 时告警
REQUIRED_FIELDS = ["id", "title", "tags", "problem", "core_idea", "reuse_when"]

# 约定：以 `_` 或 `.` 开头的目录是库的辅助目录（_inbox / _docs / .tmp…），不是论文，一律跳过。
# 论文目录请用正常名字（如 dualBrep）。
SKIP_DIRS = {"_inbox", "_templates", "_trash"}


# --------------------------------------------------------------------------
# 根目录解析
# --------------------------------------------------------------------------
def resolve_root(cli_root: str | None = None) -> Path:
    """按优先级解析论文库根目录：CLI > 环境变量 > config.json > 默认值。"""
    if cli_root:
        return Path(cli_root).expanduser().resolve()
    env = os.environ.get("PAPERLIB_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    if CONFIG_FILE.exists():
        try:
            cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            root = cfg.get("library_root")
            if root:
                return Path(root).expanduser().resolve()
        except Exception as exc:  # 配置坏了要让用户看见，不要静默吞掉
            print(f"[warn] config.json 解析失败，回退默认值：{exc}", file=sys.stderr)
    return DEFAULT_ROOT.resolve()


# --------------------------------------------------------------------------
# 极简 YAML frontmatter 读写（不引入 PyYAML 依赖）
# --------------------------------------------------------------------------
def _flow_is_incomplete(buf: str) -> bool:
    """判断一个 JSON 流式字面量（[...] / {...}）是否还没闭合。

    手写 frontmatter 时常把长列表折行写成
        tags: [
          "a",
          "b"
        ]
    单行解析会失败，必须先攒够再交给 json.loads。字符串内的括号不算。
    """
    depth = 0
    in_str = False
    esc = False
    for ch in buf:
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
    return depth > 0


def _parse_scalar(raw: str):
    raw = raw.strip()
    if raw == "":
        return None
    if raw[0] in "\"'[{":
        try:
            return json.loads(raw)
        except Exception:
            pass
    if raw in ("true", "false"):
        return raw == "true"
    if raw in ("null", "~"):
        return None
    if re.fullmatch(r"-?\d+", raw):
        return int(raw)
    return raw


def split_frontmatter(text: str) -> tuple[dict, str]:
    """切分 YAML frontmatter 与正文。返回 (meta, body)。无 frontmatter 则 meta={}。"""
    if not text.startswith("---"):
        return {}, text
    lines = text.splitlines()
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() in ("---", "..."):
            end = i
            break
    if end is None:
        return {}, text

    meta: dict = {}
    key = None
    lines_in_range = lines[1:end]
    i = 0
    while i < len(lines_in_range):
        line = lines_in_range[i]
        i += 1
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        stripped = line.strip()
        if stripped.startswith("- ") and key:          # 块状列表项
            cur = meta.get(key)
            if not isinstance(cur, list):
                cur = [] if cur is None else [cur]
            cur.append(_parse_scalar(stripped[2:]))
            meta[key] = cur
            continue
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*)$", line)
        if not m:
            continue
        key, val = m.group(1), m.group(2)
        # 流式列表/字典可能折行写成多行 JSON，攒到闭合后再一次性解析
        buf = val.strip()
        if buf[:1] in ("[", "{") and _flow_is_incomplete(buf):
            while i < len(lines_in_range) and _flow_is_incomplete(buf):
                nxt = lines_in_range[i].strip()
                i += 1
                if not nxt or nxt.startswith("#"):
                    continue
                buf += " " + nxt
            meta[key] = _parse_scalar(buf)
            continue
        if buf == "":                                  # 可能是块状列表的开头
            meta[key] = None
        else:
            meta[key] = _parse_scalar(buf)
    return meta, "\n".join(lines[end + 1:])


def render_frontmatter(meta: dict) -> str:
    """把 meta 渲染成 YAML frontmatter。标量/列表统一用 JSON 字面量，JSON 是合法 YAML。"""
    out = ["---"]
    keys = [k for k in FIELD_ORDER if k in meta] + [k for k in meta if k not in FIELD_ORDER]
    for k in keys:
        v = meta[k]
        if v == []:                       # 空列表渲染成 [] ，提示这是个列表字段
            out.append(f"{k}: []")
            continue
        if v is None or v == "":          # 空标量渲染成空值，提示这是个标量字段
            out.append(f"{k}:")
            continue
        if isinstance(v, str):
            out.append(f"{k}: {json.dumps(v, ensure_ascii=False)}")
        elif isinstance(v, bool) or isinstance(v, (int, float)):
            out.append(f"{k}: {json.dumps(v)}")
        elif isinstance(v, list):
            if all(isinstance(x, (str, int, float, bool)) for x in v):
                out.append(f"{k}: {json.dumps(v, ensure_ascii=False)}")
            else:
                out.append(f"{k}: {json.dumps(v, ensure_ascii=False)}")
        else:
            out.append(f"{k}: {json.dumps(v, ensure_ascii=False)}")
    out.append("---")
    return "\n".join(out)


def write_card(path: Path, meta: dict, body: str) -> None:
    path.write_text(f"{render_frontmatter(meta)}\n\n{body.lstrip()}\n", encoding="utf-8")


# --------------------------------------------------------------------------
# 工具函数
# --------------------------------------------------------------------------
def slugify(name: str) -> str:
    """把论文文件/标题变成安全的目录名（保留中英文与数字，剔除路径非法字符）。"""
    name = unicodedata.normalize("NFKC", name).strip()
    name = re.sub(r"[\\/:*?\"<>|\r\n\t]+", "_", name)
    name = re.sub(r"\s+", " ", name).strip(" ._")
    return name[:80] or "untitled"


def guess_title(text: str, fallback: str) -> str:
    """从全文前若干行里猜标题：取第一行长度适中、非页码噪声的行。"""
    for line in text.splitlines()[:40]:
        s = line.strip()
        if s.startswith("---") or s.startswith("```"):
            continue
        s = re.sub(r"^\s*(?:---\s*page\s*\d+\s*---)\s*", "", s).strip()
        if 12 <= len(s) <= 200 and not re.fullmatch(r"[\d\s\W]{0,6}", s):
            return s
    return fallback


def extract_text(src: Path, dest: Path) -> tuple[bool, str]:
    """尽力抽取全文到 dest。返回 (是否成功, 说明)。"""
    suffix = src.suffix.lower()
    try:
        if suffix == ".pdf":
            from pypdf import PdfReader
            reader = PdfReader(str(src))
            chunks = []
            for i, page in enumerate(reader.pages):
                try:
                    chunks.append(f"--- page {i + 1} ---\n{page.extract_text() or ''}")
                except Exception as exc:
                    chunks.append(f"--- page {i + 1} ---\n[extract failed: {exc}]")
            dest.write_text("\n\n".join(chunks), encoding="utf-8")
            return True, f"{len(reader.pages)} 页"
        if suffix in (".txt", ".md", ".tex", ".html", ".json"):
            dest.write_text(src.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
            return True, "直接复制"
    except ImportError as exc:
        return False, f"缺少解析库（{exc}）"
    except Exception as exc:
        return False, f"抽取失败：{exc}"
    return False, f"未支持的格式 {suffix}（请由 Agent 用 Read/pdf skill 转成 txt）"


def iter_paper_dirs(root: Path):
    """遍历候选论文目录。跳过隐藏目录、保留目录名、以及空目录（空目录不是论文）。"""
    if not root.exists():
        return
    for p in sorted(root.iterdir()):
        if not p.is_dir() or p.name.startswith((".", "_")) or p.name in SKIP_DIRS:
            continue
        if not any(p.iterdir()):
            continue
        yield p


def load_cards(root: Path) -> list[dict]:
    """扫描论文库，读出每篇的 frontmatter + 文件清单。"""
    papers = []
    for d in iter_paper_dirs(root):
        card = d / "README.md"
        if not card.exists():
            print(f"[warn] {d.name}/ 没有 README.md，已跳过（先跑 scaffold）", file=sys.stderr)
            continue
        meta, body = split_frontmatter(card.read_text(encoding="utf-8", errors="replace"))
        meta = {k: v for k, v in meta.items() if v not in (None, "", [])}
        meta["_dir"] = d.name
        meta["_body"] = body
        meta["_files"] = sorted(f.name for f in d.iterdir() if f.is_file())
        papers.append(meta)
    return papers


def _as_list(v) -> list:
    if v is None or v == "":
        return []
    return v if isinstance(v, list) else [v]


def one_line(s, width: int = 120) -> str:
    s = re.sub(r"\s+", " ", str(s or "")).strip()
    return s if len(s) <= width else s[: width - 1] + "…"


# --------------------------------------------------------------------------
# 子命令
# --------------------------------------------------------------------------
def cmd_config(args) -> int:
    root = resolve_root(args.root)
    print(f"skill_dir    : {SKILL_DIR}")
    print(f"config_file  : {CONFIG_FILE}  ({'存在' if CONFIG_FILE.exists() else '不存在'})")
    print(f"library_root : {root}  ({'存在' if root.exists() else '不存在'})")
    print(f"env override : PAPERLIB_ROOT={os.environ.get('PAPERLIB_ROOT') or '(未设置)'}")
    if root.exists():
        n = sum(1 for _ in iter_paper_dirs(root))
        print(f"papers       : {n} 个子目录")
    return 0


def cmd_init(args) -> int:
    root = resolve_root(args.root)
    root.mkdir(parents=True, exist_ok=True)
    (root / "_inbox").mkdir(exist_ok=True)
    print(f"✅ 论文库根目录就绪：{root}")
    print(f"   收件箱：{root / '_inbox'}（把新 PDF 丢这里，或直接给 Agent 一个路径）")
    return cmd_reindex(args)


def cmd_scaffold(args) -> int:
    root = resolve_root(args.root)
    src = Path(args.file).expanduser().resolve()
    if not src.exists():
        print(f"[error] 找不到文件：{src}", file=sys.stderr)
        return 2

    pid = slugify(args.id or src.stem)
    dest_dir = root / pid
    if dest_dir.exists() and not args.force:
        print(f"[info] 目录已存在，进入增量模式（不会覆盖已有 README.md / analysis.md）：{dest_dir}")
    dest_dir.mkdir(parents=True, exist_ok=True)

    # 1) 把原文放进论文目录（若已在目录内则跳过）
    placed = dest_dir / src.name
    if src.parent != dest_dir:
        if not placed.exists():
            shutil.copy2(src, placed)
            print(f"  原文已入库：{pid}/{src.name}")
        else:
            print(f"  原文已存在，跳过复制：{pid}/{src.name}")

    # 2) 全文抽取
    fulltext = dest_dir / f"{Path(src.name).stem}_extracted.txt"
    if fulltext.exists():
        print(f"  全文已存在，跳过抽取：{pid}/{fulltext.name}")
    else:
        ok, note = extract_text(placed, fulltext)
        print(f"  全文抽取：{'✅' if ok else '⚠️'} {note} -> {fulltext.name if ok else '(未生成)'}")

    sample = fulltext.read_text(encoding="utf-8", errors="replace")[:8000] if fulltext.exists() else ""

    # 3) 特性卡脚手架
    card = dest_dir / "README.md"
    if card.exists():
        print("  特性卡 README.md 已存在，跳过（避免覆盖你已写的内容）")
    else:
        tpl = (SKILL_DIR / "assets" / "templates" / "feature-card.md").read_text(encoding="utf-8")
        meta = {
            "id": pid,
            "title": args.title or guess_title(sample, src.stem),
            "authors": [],
            "year": None,
            "venue": "",
            "source": src.name,
            "tags": [],
            "task": [],
            "problem": "",
            "core_idea": "",
            "key_mechanisms": [],
            "results": "",
            "limitations": [],
            "reuse_when": [],
            "related": [],
            "analysis": "analysis.md",
            "status": "pending",
            "created": _dt.date.today().isoformat(),
        }
        write_card(card, meta, tpl)
        print(f"  特性卡脚手架：{pid}/README.md（status=pending，待 Agent 填写）")

    # 4) 分析文档脚手架
    ana = dest_dir / "analysis.md"
    if not ana.exists():
        ana.write_text(
            (SKILL_DIR / "assets" / "templates" / "analysis.md").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        print(f"  分析文档脚手架：{pid}/analysis.md")

    print()
    print("── 下一步（这部分必须由 Agent 做，脚本不代劳）──")
    print(f"  1. 读 {pid}/{fulltext.name}")
    print(f"  2. 填 {pid}/README.md 的 frontmatter：tags/task/problem/core_idea/reuse_when 为必填")
    print(f"  3. 写 {pid}/analysis.md")
    print(f"  4. 把 README.md 的 status 改成 summarized，再跑：library.py reindex")
    return 0


def cmd_reindex(args) -> int:
    root = resolve_root(args.root)
    papers = load_cards(root)
    today = _dt.datetime.now().astimezone().isoformat(timespec="seconds")

    # 排序：tags 越丰富越靠前，便于人工浏览
    papers.sort(key=lambda m: (-len(_as_list(m.get("tags"))), str(m.get("id") or m["_dir"]).lower()))

    for m in papers:
        missing = [f for f in REQUIRED_FIELDS if not m.get(f)]
        if missing:
            print(f"[warn] {m['_dir']}/README.md 缺少必填字段：{', '.join(missing)}", file=sys.stderr)

    index = {
        "library_root": str(root),
        "generated": today,
        "count": len(papers),
        "papers": [
            {
                **{k: v for k, v in m.items() if not k.startswith("_")},
                "dir": m["_dir"],
                "files": m["_files"],
            }
            for m in papers
        ],
    }
    (root / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # INDEX.md —— 给人和 Agent 都读得懂的一览表
    lines = [
        "# 论文库索引",
        "",
        f"> 自动生成，请勿手工编辑。最后更新：{today}　共 **{len(papers)}** 篇。",
        "> 每篇的权威内容在 `<id>/README.md`（特性卡）与 `<id>/analysis.md`（深度分析）。",
        "",
        "## 一览",
        "",
        "| id | 年份 | 一句话核心思路 | tags | 状态 |",
        "|----|------|----------------|------|------|",
    ]
    for m in papers:
        lines.append(
            "| [{id}]({d}/README.md) | {y} | {idea} | {tags} | {st} |".format(
                id=m.get("id") or m["_dir"],
                d=m["_dir"],
                y=m.get("year") or "—",
                idea=one_line(m.get("core_idea"), 80) or "_(待填写)_",
                tags=", ".join(str(t) for t in _as_list(m.get("tags"))[:6]) or "—",
                st=m.get("status") or "?",
            )
        )

    # 按标签聚合，便于「我想找做过 X 的论文」
    tag_map: dict[str, list[str]] = {}
    for m in papers:
        for t in _as_list(m.get("tags")) + _as_list(m.get("task")):
            tag_map.setdefault(str(t), []).append(m.get("id") or m["_dir"])
    if tag_map:
        lines += ["", "## 按标签聚合", ""]
        for t in sorted(tag_map, key=lambda k: (-len(tag_map[k]), k.lower())):
            lines.append(f"- **{t}**（{len(tag_map[t])}）：{', '.join(sorted(tag_map[t]))}")

    # 按「什么时候可以复用」聚合 —— 这是跨问题检索的主要入口
    reuse_rows = [(m.get("id") or m["_dir"], _as_list(m.get("reuse_when"))) for m in papers]
    if any(r for _, r in reuse_rows):
        lines += ["", "## 何时可复用（reuse_when）", ""]
        for pid, items in reuse_rows:
            for it in items:
                lines.append(f"- `{pid}` → {one_line(it, 140)}")

    lines += [
        "",
        "## 字段说明",
        "",
        "见 skill 的 `references/feature-card-schema.md`。检索协议见 `references/retrieval-protocol.md`。",
        "",
    ]
    (root / "INDEX.md").write_text("\n".join(lines), encoding="utf-8")

    print(f"✅ 已重建索引：{root / 'index.json'} 与 {root / 'INDEX.md'}（{len(papers)} 篇）")
    return 0


def _score(paper: dict, terms: list[str]) -> int:
    """给论文打分：标签/任务词精确命中权重最高，其次标题，最后正文摘要。"""
    tags = [str(t).lower() for t in _as_list(paper.get("tags")) + _as_list(paper.get("task"))]
    title = str(paper.get("title") or "").lower()
    idea = str(paper.get("core_idea") or "").lower()
    prob = str(paper.get("problem") or "").lower()
    reuse = " ".join(str(x) for x in _as_list(paper.get("reuse_when"))).lower()
    mech = " ".join(str(x) for x in _as_list(paper.get("key_mechanisms"))).lower()
    body = str(paper.get("_body") or "").lower()

    score = 0
    for t in terms:
        t = t.lower()
        score += 10 * sum(1 for tag in tags if t == tag)
        score += 5 * sum(1 for tag in tags if t in tag)
        if t in title:
            score += 6
        if t in idea:
            score += 4
        if t in prob:
            score += 3
        if t in reuse:
            score += 3
        if t in mech:
            score += 2
        if t in body:
            score += 1
    return score


def cmd_search(args) -> int:
    root = resolve_root(args.root)
    if not root.exists():
        print(f"[error] 论文库不存在：{root}", file=sys.stderr)
        return 2
    papers = load_cards(root)
    if not papers:
        print("（论文库为空）")
        return 0

    terms = [t for t in re.split(r"[\s,;]+", args.query.strip()) if t] if args.query else []
    hits = []
    for m in papers:
        if args.tag and not any(args.tag.lower() in str(t).lower() for t in _as_list(m.get("tags"))):
            continue
        if args.task and not any(args.task.lower() in str(t).lower() for t in _as_list(m.get("task"))):
            continue
        s = _score(m, terms) if terms else 1
        if s > 0:
            hits.append((s, m))
    hits.sort(key=lambda x: (-x[0], str(x[1].get("id") or x[1]["_dir"]).lower()))
    hits = hits[: args.limit]

    if args.json:
        print(json.dumps(
            [{"score": s, **{k: v for k, v in m.items() if not k.startswith("_")}, "dir": m["_dir"]}
             for s, m in hits],
            ensure_ascii=False, indent=2))
        return 0

    if not hits:
        print("未命中。建议放宽关键词，或先跑 `list` 看看库里有什么标签。")
        return 1

    for s, m in hits:
        pid = m.get("id") or m["_dir"]
        print(f"\n[{s:>3}] {pid}  ({m.get('year') or '—'})")
        print(f"      title    : {one_line(m.get('title'), 150)}")
        print(f"      tags     : {', '.join(str(t) for t in _as_list(m.get('tags'))) or '—'}")
        print(f"      problem  : {one_line(m.get('problem'), 150) or '—'}")
        print(f"      core_idea: {one_line(m.get('core_idea'), 150) or '—'}")
        for r in _as_list(m.get("reuse_when"))[:3]:
            print(f"      reuse    : {one_line(r, 150)}")
        print(f"      卡片     : {root / m['_dir'] / 'README.md'}")
    return 0


def cmd_list(args) -> int:
    root = resolve_root(args.root)
    papers = load_cards(root)
    if not papers:
        print(f"（{root} 下暂无论文）")
        return 0
    print(f"论文库：{root}　共 {len(papers)} 篇\n")
    for m in papers:
        print(f"- {m.get('id') or m['_dir']:<28} {m.get('year') or '    '}  "
              f"[{m.get('status') or '?'}]  {one_line(m.get('title'), 90)}")
    return 0


# --------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="library.py",
        description="paper-library 论文库工具：脚手架 / 抽取 / 建索引 / 检索",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--root", help="论文库根目录（覆盖 config.json 与环境变量）")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("config", help="显示解析后的路径与配置来源")
    sub.add_parser("init", help="初始化论文库根目录（建 _inbox 并建索引）")

    sp = sub.add_parser("scaffold", help="把一篇新论文入库：建目录 + 抽全文 + 生成卡片脚手架")
    sp.add_argument("file", help="论文文件路径（pdf/txt/md/tex 等）")
    sp.add_argument("--id", help="目录名（默认取文件名）")
    sp.add_argument("--title", help="标题（默认从全文首行猜）")
    sp.add_argument("--force", action="store_true", help="目录已存在时也继续（仍不覆盖已有卡片）")

    sub.add_parser("reindex", help="从各 README.md 重建 index.json 与 INDEX.md")

    ss = sub.add_parser("search", help="按关键词/标签检索论文特性")
    ss.add_argument("query", nargs="?", default="", help="空格分隔的关键词")
    ss.add_argument("--tag", help="只在含该 tag 的论文里搜")
    ss.add_argument("--task", help="只在含该 task 的论文里搜")
    ss.add_argument("--limit", type=int, default=10)
    ss.add_argument("--json", action="store_true", help="输出 JSON")

    sub.add_parser("list", help="列出论文库全部论文")

    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return {
        "config": cmd_config, "init": cmd_init, "scaffold": cmd_scaffold,
        "reindex": cmd_reindex, "search": cmd_search, "list": cmd_list,
    }[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
