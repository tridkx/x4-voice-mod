#!/usr/bin/env python3
"""X4 Foundations cat/dat 归档工具（索引解析 / 列表 / 提取 / 打包）。

X4 的归档格式非常简单，不需要 XRCatTool：

    .cat  = 纯文本索引，每行一个条目：
            <相对路径> <字节大小> <mtime> <md5>
    .dat  = 所有文件按 .cat 的行顺序**首尾相接**紧密排列，无对齐、无压缩。
            offset(i) = sum(size(0..i-1))

因此读取只需解析文本 + 累加偏移。

用法：
    python x4cat.py list   [--game DIR] [--pattern REGEX]
    python x4cat.py extract --pattern REGEX [--out DIR] [--game DIR] [--cat NAME]
    python x4cat.py info   [--game DIR]

游戏目录解析顺序：--game > $X4_GAME_DIR > 候选列表自动探测。
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

# 候选安装位置（仅用于自动探测，不是硬依赖）。可用 --game 或 $X4_GAME_DIR 覆盖。
_CANDIDATE_SUBPATHS = (
    "steamapps/common/X4 Foundations",
    "SteamLibrary/steamapps/common/X4 Foundations",
    "Program Files (x86)/Steam/steamapps/common/X4 Foundations",
    "GOG Games/X4 Foundations",
)


def find_game_dir(explicit: str | None = None) -> Path:
    """按 显式参数 -> 环境变量 -> 候选路径 的顺序定位游戏根目录。"""
    if explicit:
        p = Path(explicit).expanduser()
        if not (p / "X4.exe").is_file():
            raise SystemExit(f"指定的游戏目录里没有 X4.exe: {p}")
        return p

    env = os.environ.get("X4_GAME_DIR")
    if env:
        p = Path(env).expanduser()
        if (p / "X4.exe").is_file():
            return p
        raise SystemExit(f"$X4_GAME_DIR 里没有 X4.exe: {p}")

    roots: list[Path] = []
    for drive in ("C:", "D:", "E:", "F:", "G:"):
        d = Path(drive + "/")
        if d.exists():
            roots.append(d)
    home = Path.home()

    seen: set[Path] = set()
    for root in roots:
        for sub in _CANDIDATE_SUBPATHS:
            cand = (root / sub).resolve() if not root.drive == home.drive else (root / sub)
            if cand in seen:
                continue
            seen.add(cand)
            if (cand / "X4.exe").is_file():
                return cand
    raise SystemExit(
        "未能自动定位 X4 安装目录。请用 --game <目录> 或在环境变量 X4_GAME_DIR 中指定。"
    )


@dataclass(frozen=True)
class Entry:
    path: str          # 归档内的相对路径，正斜杠
    size: int
    mtime: int
    md5: str
    offset: int        # .dat 内的字节偏移
    archive: str       # 来源归档标签，"01" / "ego_dlc_boron" 等
    cat_path: Path     # 来源 .cat 的绝对路径（.dat 由它推导）


def read_index(cat_path: Path, archive: str) -> list[Entry]:
    """解析单个 .cat 文本索引，返回带累计偏移的条目列表。"""
    entries: list[Entry] = []
    offset = 0
    with cat_path.open("r", encoding="utf-8", errors="surrogateescape") as fh:
        for lineno, raw in enumerate(fh, 1):
            line = raw.rstrip("\r\n")
            if not line:
                continue
            # 路径本身不含空格，因此从右侧切 3 段安全
            parts = line.rsplit(" ", 3)
            if len(parts) != 4:
                raise SystemExit(f"{cat_path}:{lineno} 索引行格式异常: {line!r}")
            path, size_s, mtime_s, md5 = parts
            size = int(size_s)
            entries.append(
                Entry(
                    path=path.replace("\\", "/"),
                    size=size,
                    mtime=int(mtime_s),
                    md5=md5,
                    offset=offset,
                    archive=archive,
                    cat_path=cat_path,
                )
            )
            offset += size
    return entries


def load_all(game: Path, only: list[str] | None = None) -> dict[str, Entry]:
    """按归档编号顺序载入全部索引；同名路径后者覆盖前者（与游戏加载顺序一致）。"""
    cats: list[tuple[str, Path]] = []
    # 基础归档 01..09
    for cat in sorted(game.glob("[0-9][0-9].cat")):
        cats.append((cat.stem, cat))
    # DLC / 扩展归档
    ext_root = game / "extensions"
    if ext_root.is_dir():
        for sub in sorted(ext_root.iterdir()):
            if not sub.is_dir():
                continue
            for cat in sorted(sub.glob("*.cat")):
                if cat.stem.endswith("_sig"):   # 签名归档不含数据
                    continue
                if not cat.with_suffix(".dat").is_file():
                    continue
                cats.append((f"{sub.name}/{cat.stem}", cat))

    if only:
        wanted = {o.lower() for o in only}
        cats = [
            (a, c)
            for (a, c) in cats
            if a.lower() in wanted
            or Path(a).name.lower() in wanted
            or c.stem.lower() in wanted
            or Path(a).parent.name.lower() in wanted
        ]

    index: dict[str, Entry] = {}
    for archive, cat in cats:
        for e in read_index(cat, archive):
            index[e.path] = e
    return index


def dat_path_for(game: Path, entry: Entry) -> Path:
    """由来源 .cat 路径推导同名 .dat 路径。"""
    return entry.cat_path.with_suffix(".dat")


def read_file(game: Path, entry: Entry) -> bytes:
    dat = dat_path_for(game, entry)
    with dat.open("rb") as fh:
        fh.seek(entry.offset)
        data = fh.read(entry.size)
    if len(data) != entry.size:
        raise SystemExit(f"读取越界: {entry.path} (期望 {entry.size} 实得 {len(data)})")
    return data


def cmd_info(args: argparse.Namespace) -> None:
    game = find_game_dir(args.game)
    print(f"游戏目录: {game}")
    for cat in sorted(game.glob("[0-9][0-9].cat")):
        n = sum(1 for _ in cat.open("r", encoding="utf-8", errors="surrogateescape"))
        print(f"  {cat.name:8} {n:7d} 条目   dat={cat.with_suffix('.dat').stat().st_size:,} 字节")


def cmd_list(args: argparse.Namespace) -> None:
    game = find_game_dir(args.game)
    index = load_all(game, args.cat)
    pat = re.compile(args.pattern, re.I) if args.pattern else None
    hits = [e for e in index.values() if pat is None or pat.search(e.path)]
    if args.prefix_stats:
        from collections import Counter

        c: Counter[str] = Counter()
        for e in hits:
            depth = args.prefix_stats
            c["/".join(e.path.split("/")[:depth])] += 1
        for k, v in c.most_common(args.limit or 50):
            print(f"{v:8d}  {k}")
        return
    hits.sort(key=lambda e: e.path)
    for e in hits[: args.limit or 200]:
        print(f"{e.size:10d}  {e.archive:12}  {e.path}")
    print(f"--- 共 {len(hits)} 条匹配")


def cmd_extract(args: argparse.Namespace) -> None:
    game = find_game_dir(args.game)
    out = Path(args.out).expanduser() if args.out else Path.cwd() / "extracted"
    index = load_all(game, args.cat)
    pat = re.compile(args.pattern, re.I) if args.pattern else None
    hits = [e for e in index.values() if pat is None or pat.search(e.path)]
    hits.sort(key=lambda e: e.path)
    if not hits:
        raise SystemExit("没有匹配的条目")
    total = 0
    for e in hits:
        dest = out / e.path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(read_file(game, e))
        total += e.size
    print(f"提取 {len(hits)} 个文件 / {total:,} 字节 -> {out}")


def cmd_pack(args: argparse.Namespace) -> None:
    """把目录打包成 X4 的 <name>.cat + <name>.dat。

    cat 是纯文本索引（路径 大小 mtime md5），dat 是文件按索引顺序的紧密拼接。
    """
    src = Path(args.src).expanduser().resolve()
    if not src.is_dir():
        raise SystemExit(f"源目录不存在: {src}")
    out = Path(args.out).expanduser() if args.out else src.parent / src.name
    out.parent.mkdir(parents=True, exist_ok=True)

    files = sorted(p for p in src.rglob("*") if p.is_file())
    if args.exclude_sig:
        files = [p for p in files if not p.name.endswith(".sig")]
    for pattern in args.exclude or []:
        files = [
            p for p in files
            if not Path(p.relative_to(src).as_posix()).match(pattern) and p.name != pattern
        ]
    if not files:
        raise SystemExit(f"{src} 下没有文件")

    # 先算索引，再写 dat，保证两者顺序一致
    entries: list[tuple[str, int, int, str, Path]] = []
    for p in files:
        rel = p.relative_to(src).as_posix()
        data_len = p.stat().st_size
        mtime = int(p.stat().st_mtime)
        digest = hashlib.md5(p.read_bytes()).hexdigest()
        entries.append((rel, data_len, mtime, digest, p))
    # X4 按 .cat 行顺序在 .dat 里紧密排列，这里保持排序后的顺序
    entries.sort(key=lambda e: e[0])

    cat_path = out.with_suffix(".cat")
    dat_path = out.with_suffix(".dat")
    with dat_path.open("wb") as dat, cat_path.open("w", encoding="utf-8", newline="\n") as cat:
        for rel, size, mtime, digest, p in entries:
            cat.write(f"{rel} {size} {mtime} {digest}\n")
            dat.write(p.read_bytes())
    total = sum(e[1] for e in entries)
    print(f"打包 {len(entries)} 个文件 / {total:,} 字节")
    print(f"  {cat_path}")
    print(f"  {dat_path}")


def main() -> None:
    ap = argparse.ArgumentParser(description="X4 cat/dat 归档工具")
    ap.add_argument("--game", help="游戏根目录（默认自动探测 / $X4_GAME_DIR）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("info", help="列出各归档条目数与 dat 大小")
    p.set_defaults(func=cmd_info)

    p = sub.add_parser("list", help="按正则列出归档内容")
    p.add_argument("--pattern", help="路径正则（忽略大小写）")
    p.add_argument("--cat", nargs="*", help="只扫描指定归档，如 03 或 ego_dlc_boron")
    p.add_argument("--prefix-stats", type=int, metavar="N", help="按前 N 级目录汇总计数")
    p.add_argument("--limit", type=int)
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("extract", help="按正则提取到目录")
    p.add_argument("--pattern", required=True, help="路径正则（忽略大小写）")
    p.add_argument("--out", help="输出目录（默认 ./extracted）")
    p.add_argument("--cat", nargs="*", help="只扫描指定归档")
    p.set_defaults(func=cmd_extract)

    p = sub.add_parser("pack", help="把目录打包成 cat/dat")
    p.add_argument("--src", required=True, help="源目录（目录内容即归档根）")
    p.add_argument("--out", help="输出的 cat/dat 基名（默认 <src>/../<src 名>）")
    p.add_argument("--exclude-sig", action="store_true", default=True,
                   help="跳过 .sig 文件（默认开启）")
    p.add_argument("--exclude", action="append", metavar="GLOB",
                   help="排除匹配的文件（可多次），如 content.xml")
    p.set_defaults(func=cmd_pack)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
