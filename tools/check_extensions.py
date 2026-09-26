#!/usr/bin/env python3
"""X4 扩展体检：检查 extensions 下每个 mod 能否被游戏正常识别。

排查「装进 extensions 却在游戏扩展列表里看不到」这类问题。
X4 遇到解析不了的 content.xml 会**静默忽略整个扩展**，没有任何提示，
所以这里逐项验：

  1. content.xml 是否存在、是否是合法 XML（最常见的坑：属性里裸奔 & < >）
  2. content.xml 的 id 是否与目录名一致
  3. .cat 与 .dat 是否成对、dat 实际大小是否等于索引里所有 size 之和
  4. .cat 索引每一行是否能解析、dat 是否能完整读出每个条目（抽检）

用法：
    python check_extensions.py [--game DIR] [--ext 名字] [--deep]
"""

from __future__ import annotations

import argparse
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from x4cat import find_game_dir, read_file, read_index  # noqa: E402


def check_one(ext_dir: Path, deep: bool = False) -> tuple[list[str], list[str]]:
    """返回 (问题, 提示)。问题会真正导致扩展失效，提示只是供参考。"""
    problems: list[str] = []
    notes: list[str] = []
    cx = ext_dir / "content.xml"

    if not cx.is_file():
        problems.append("缺 content.xml（X4 不会把它当成扩展）")
    else:
        try:
            root = ET.parse(cx).getroot()
        except ET.ParseError as exc:
            problems.append(f"content.xml 不是合法 XML -> X4 会静默忽略整个扩展: {exc}")
            root = None
        if root is not None:
            if root.tag != "content":
                problems.append(f"content.xml 根元素是 <{root.tag}>，应该是 <content>")
            cid = root.get("id")
            if cid and cid != ext_dir.name:
                # 实测大量正常工作的 mod 都这样，所以只是提示
                notes.append(f"content.xml 的 id='{cid}' 与目录名 '{ext_dir.name}' 不同（通常无碍）")

    cats = sorted(p for p in ext_dir.glob("*.cat") if not p.stem.endswith("_sig"))
    if not cats:
        # 纯脚本类扩展可以没有 cat（直接放 md/ 等目录），不算错
        if not any((ext_dir / d).is_dir() for d in ("md", "libraries", "aiscripts", "t")):
            problems.append("既没有 .cat/.dat 也没有散装资源目录")

    for cat in cats:
        dat = cat.with_suffix(".dat")
        if not dat.is_file():
            problems.append(f"{cat.name} 没有配套的 {dat.name}")
            continue
        try:
            entries = read_index(cat, cat.stem)
        except SystemExit as exc:
            problems.append(f"{cat.name} 索引解析失败: {exc}")
            continue
        total = sum(e.size for e in entries)
        actual = dat.stat().st_size
        if total != actual:
            problems.append(
                f"{cat.name}: 索引 size 合计 {total:,} != {dat.name} 实际 {actual:,}"
                "（dat 与 cat 不匹配，读取会错位）"
            )
        if deep and entries:
            step = max(1, len(entries) // 5)
            for e in entries[::step]:
                try:
                    data = read_file(None, e)
                except SystemExit as exc:
                    problems.append(f"{cat.name}: 读出 {e.path} 失败: {exc}")
                    break
                if len(data) != e.size:
                    problems.append(f"{cat.name}: {e.path} 长度不符")
                    break
    return problems, notes


def main() -> None:
    ap = argparse.ArgumentParser(description="X4 扩展体检")
    ap.add_argument("--game", help="游戏根目录（默认自动探测）")
    ap.add_argument("--ext", help="只检查指定扩展")
    ap.add_argument("--deep", action="store_true", help="额外抽检 dat 内容可读性")
    args = ap.parse_args()

    game = find_game_dir(args.game)
    root = game / "extensions"
    if not root.is_dir():
        raise SystemExit(f"没有 extensions 目录: {root}")

    dirs = sorted(d for d in root.iterdir() if d.is_dir())
    if args.ext:
        dirs = [d for d in dirs if d.name == args.ext]
        if not dirs:
            raise SystemExit(f"没找到扩展: {args.ext}")

    bad = 0
    for d in dirs:
        problems, notes = check_one(d, args.deep)
        if problems:
            bad += 1
            print(f"[问题] {d.name}")
            for p in problems:
                print(f"        - {p}")
        else:
            print(f"[正常] {d.name}")
        for n in notes:
            print(f"        · {n}")
    print(f"\n共 {len(dirs)} 个扩展，{bad} 个有问题")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
