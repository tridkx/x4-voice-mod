#!/usr/bin/env python3
"""原地替换基础归档里的文件 —— 语音 mod 的兜底方案。

为什么需要它：X4 的**音频系统**似乎不参与扩展的文件覆盖（实测扩展里的
`voice-l044/...` 与 `<sample>` 重定向都听不到效果），而脚本/贴图/UI 资源
的覆盖是正常的（starwarsmod 覆盖了 589 个基础文件 + 13 张纹理）。
所以最后一条确定性路线就是：**直接改基础归档本身**。

归档格式决定了这活很好干：
    offset(i) = size(0..i-1) 的累加
所以只要新文件与旧文件**字节数完全相同**，就能原地覆盖写、其它文件全不受影响。
做法是：新音频编码后若小于原大小，就在末尾补 0 补齐（Ogg 分页解码会忽略尾部垃圾）。

用法：
    # 1) 先看看能替换多少（不写盘）
    python patch_base_archive.py --cat 03 --replace-dir build/audio --dry-run

    # 2) 真正写入（自动备份 .cat，并记录 .dat 原大小）
    python patch_base_archive.py --cat 03 --replace-dir build/audio --apply

    # 3) 回滚
    python patch_base_archive.py --cat 03 --restore
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from x4cat import find_game_dir, read_index  # noqa: E402

PAD_BYTE = b"\x00"


def ogg_is_padded(data: bytes) -> bool:
    """粗略判断尾部是否已有填充（最后一个 OggS 页之后还有多余字节）。"""
    last = data.rfind(b"OggS")
    if last < 0:
        return False
    # 解析最后一页长度
    segs = data[last + 26]
    body = sum(data[last + 27:last + 27 + segs])
    return last + 27 + segs + body < len(data)


def cmd_run(args: argparse.Namespace) -> None:
    game = find_game_dir(args.game)
    cat = game / f"{args.cat}.cat"
    dat = game / f"{args.dat or args.cat}.dat"
    if not cat.is_file() or not dat.is_file():
        raise SystemExit(f"找不到归档: {cat} / {dat}")

    entries = read_index(cat, args.cat)
    by_path = {e.path: e for e in entries}
    repl_dir = Path(args.replace_dir).expanduser().resolve()
    if not repl_dir.is_dir():
        raise SystemExit(f"替换目录不存在: {repl_dir}")

    pairs = []
    for f in sorted(repl_dir.rglob("*")):
        if not f.is_file():
            continue
        rel = f.relative_to(repl_dir).as_posix()
        e = by_path.get(rel)
        if e is None:
            print(f"  [跳过] 归档里没有这个路径: {rel}")
            continue
        pairs.append((e, f))

    ok, too_big = [], []
    for e, f in pairs:
        new_size = f.stat().st_size
        (ok if new_size <= e.size else too_big).append((e, f, new_size))

    print(f"归档 {cat.name}: {len(entries)} 条目")
    print(f"待替换 {len(pairs)} 个，其中：装得下 {len(ok)}，超体积 {len(too_big)}")
    for e, f, s in too_big[:5]:
        print(f"  [超体积] {e.path}  新 {s} > 原 {e.size}")

    if args.dry_run:
        print("\n(dry-run，未写盘)")
        return

    if not ok:
        raise SystemExit("没有任何文件能替换，检查 --replace-dir")

    # 备份
    bak_cat = cat.with_suffix(".cat.bak-annc")
    bak_meta = cat.with_suffix(".cat.bak-annc.json")
    if not bak_cat.exists():
        shutil.copy2(cat, bak_cat)
        bak_meta.write_text(json.dumps({
            "dat": dat.name, "dat_size": dat.stat().st_size,
            "note": "还原：把 .bak-annc 覆盖回 .cat，并把 .dat 截断回 dat_size",
        }, indent=2), encoding="utf-8")
        print(f"已备份: {bak_cat.name} 与 {bak_meta.name}")

    new_lines = list(cat.read_text(encoding="utf-8", errors="surrogateescape").splitlines())
    line_of = {}
    for i, ln in enumerate(new_lines):
        if ln:
            line_of[ln.rsplit(" ", 3)[0]] = i

    written = 0
    with dat.open("r+b") as fh:
        for e, f, _s in ok:
            data = f.read_bytes()
            if len(data) < e.size:
                data = data + PAD_BYTE * (e.size - len(data))
            fh.seek(e.offset)
            fh.write(data)
            # 更新 cat 里的 md5（size 保持不变以维持偏移）
            idx = line_of[e.path]
            new_lines[idx] = f"{e.path} {e.size} {e.mtime} {hashlib.md5(data).hexdigest()}"
            written += 1
    cat.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    print(f"\n已原地替换 {written} 个文件；cat 索引的 md5 已同步更新")


def cmd_restore(args: argparse.Namespace) -> None:
    game = find_game_dir(args.game)
    cat = game / f"{args.cat}.cat"
    dat = game / f"{args.dat or args.cat}.dat"
    bak_cat = cat.with_suffix(".cat.bak-annc")
    bak_meta = cat.with_suffix(".cat.bak-annc.json")
    if not bak_cat.is_file() or not bak_meta.is_file():
        raise SystemExit("没有找到备份，无法回滚")
    meta = json.loads(bak_meta.read_text(encoding="utf-8"))
    size = meta["dat_size"]
    with dat.open("r+b") as fh:
        fh.truncate(size)
    shutil.copy2(bak_cat, cat)
    print(f"已回滚 {cat.name} 与 {dat.name}（dat 截断回 {size:,} 字节）")


def main() -> None:
    ap = argparse.ArgumentParser(description="原地替换 X4 基础归档里的文件")
    ap.add_argument("--game", help="游戏根目录（默认自动探测）")
    ap.add_argument("--cat", default="03", help="归档编号（语音在 03）")
    ap.add_argument("--dat", help="dat 基名（默认与 cat 相同）")
    ap.add_argument("--replace-dir", help="替换文件目录，内部按归档相对路径摆放")
    ap.add_argument("--dry-run", action="store_true", help="只报告，不写盘")
    ap.add_argument("--apply", action="store_true", help="实际写入")
    ap.add_argument("--restore", action="store_true", help="回滚到备份")
    args = ap.parse_args()
    if args.restore:
        cmd_restore(args)
    elif args.apply or args.dry_run:
        cmd_run(args)
    else:
        ap.error("需要 --dry-run / --apply / --restore 之一")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
