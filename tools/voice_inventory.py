#!/usr/bin/env python3
"""生成 X4 语音清单：把归档里的 voice 文件与 t 文件文本交叉，输出 CSV。

语音文件路径规则（从 sound_library.xml 与归档内容反推）：

    voice-l<LANG>/<page>/<context>/<line>.ogg     音频
    voice-l<LANG>/<page>/lipsync/<line>.xpm       口型数据

其中：
  page    = t 文件里的 <page id>，代表"说话人 / 角色音色库"
  context = normal | comm | comm_npc | comm_broadcast（同一句台词的不同播放场合）
  line    = t 文件里的 <t id>，即具体台词

用法：
    python voice_inventory.py [--game DIR] [--lang l086] [--out work/voice_inventory.csv]
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from x4cat import find_game_dir, load_all, read_file  # noqa: E402

VOICE_RE = re.compile(r"^voice-(l\d+)/(\d+)/([a-z_]+)/([^/]+)\.(ogg|xpm)$", re.I)


def parse_tfile(data: bytes) -> tuple[dict[str, dict[str, str]], dict[str, dict[str, str]]]:
    """从 t/0001-l0XX.xml 提取 page 信息与 page->{line: text}。

    返回 (pages, texts)：pages[page_id] = {"title":.., "voice":.., "descr":..}
    """
    txt = data.decode("utf-8", errors="replace")
    pages: dict[str, dict[str, str]] = {}
    texts: dict[str, dict[str, str]] = defaultdict(dict)

    page_re = re.compile(r"<page\s+id=\"(\d+)\"([^>]*)>")
    for m in page_re.finditer(txt):
        pid, attrs = m.group(1), m.group(2)
        info = {}
        for key in ("title", "descr", "voice"):
            am = re.search(rf'{key}="([^"]*)"', attrs)
            if am:
                info[key] = am.group(1)
        pages[pid] = info

    t_re = re.compile(r'<t\s+id="([^"]+)"[^>]*>(.*?)</t>', re.S)
    # 按 page 分段处理，避免跨页串味
    bounds = [(m.start(), m.group(1)) for m in page_re.finditer(txt)]
    bounds.append((len(txt), None))
    for i in range(len(bounds) - 1):
        start, pid = bounds[i]
        end = bounds[i + 1][0]
        if pid is None:
            continue
        for tm in t_re.finditer(txt, start, end):
            texts[pid][tm.group(1)] = re.sub(r"<[^>]+>", "", tm.group(2)).strip()
    return pages, texts


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--game")
    ap.add_argument("--lang", default="l086", help="用于取台词文本的 t 文件语言（默认中文 l086）")
    ap.add_argument("--tcat", default="09", help="从哪个归档读 t 文件（基础游戏文本在 09）")
    ap.add_argument("--out", default="work/voice_inventory.csv")
    args = ap.parse_args()

    game = find_game_dir(args.game)
    index = load_all(game)
    base_index = load_all(game, [args.tcat]) if args.tcat else index

    # 1) 扫归档，收集语音条目
    per_page: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    lang_counts: Counter[str] = Counter()
    for e in index.values():
        m = VOICE_RE.match(e.path)
        if not m:
            continue
        lang, page, context, line, ext = m.groups()
        lang = lang.lower()
        lang_counts[f"{lang}/{ext.lower()}"] += 1
        if ext.lower() == "ogg":
            per_page[page][context].add(line)

    # 2) t 文件文本
    tname = f"t/0001-{args.lang}.xml"
    t_entry = base_index.get(tname)
    if t_entry is None:
        cands = [p for p in base_index if p.lower() == tname.lower()]
        t_entry = base_index[cands[0]] if cands else None
    if t_entry is None:
        raise SystemExit(f"归档 {args.tcat} 里找不到 {tname}")
    pages, texts = parse_tfile(read_file(game, t_entry))

    # 3) 输出 CSV
    out = Path(args.out)
    if not out.is_absolute():
        out = Path(__file__).resolve().parent.parent / out
    out.parent.mkdir(parents=True, exist_ok=True)
    rows = 0
    with out.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["page", "page_title", "voice_page", "context", "line", "text"])
        for page in sorted(per_page, key=lambda p: -sum(len(v) for v in per_page[p].values())):
            info = pages.get(page, {})
            title = info.get("title", "?")
            for context in sorted(per_page[page]):
                for line in sorted(per_page[page][context], key=lambda s: (len(s), s)):
                    w.writerow([page, title, info.get("voice", ""), context, line,
                                texts.get(page, {}).get(line, "")])
                    rows += 1
    print(f"音频文件语言分布: {dict(lang_counts)}")
    print(f"语音 page 数: {len(per_page)}；t 文件 page 数: {len(pages)}")
    print(f"CSV 行数: {rows} -> {out}")

    # 4) 概览
    print("\n=== 语音 page 概览（按去重台词条数排序，前 45）===")
    ranked = sorted(per_page.items(), key=lambda kv: -len(set().union(*kv[1].values())))
    for page, ctxs in ranked[:45]:
        n = len(set().union(*ctxs.values()))
        info = pages.get(page, {})
        print(f"  {page:>7} {n:5d}条 {info.get('voice','?'):>3}  "
              f"{info.get('title', '?')[:58]:<58} | {info.get('descr','')[:40]}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
