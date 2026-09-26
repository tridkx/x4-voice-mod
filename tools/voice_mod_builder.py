#!/usr/bin/env python3
"""X4 语音替换 mod 构建器：把"替换计划"编译成一个可安装的扩展。

计划文件（JSON）示例：

    {
      "id": "x4_voice_probe",
      "name": "Voice Probe",
      "description": "声音替换测试",
      "langs": ["l044"],                     // 要生成哪几套语音语言目录
      "targets": [
        {
          "page": "10002",                   // 说话人 page（见 voice_inventory.csv）
          "contexts": ["normal", "comm", "comm_npc", "comm_broadcast"],
          "lines": "all",                    // "all" 或 ["1","2","3"]
          "tts_text": "测试甲",               // 生成语音的文本
          "voice": "Microsoft Huihui Desktop", // 可选，SAPI 声音名
          "rate": 0                          // 可选，语速 -10..10
        }
      ]
    }

音频处理约定（由原版资产实测得出）：
    X4 语音为 Ogg Vorbis、单声道、44100 Hz、约 48 kbps 标称码率。

用法：
    python voice_mod_builder.py --plan plan.json --out dist/x4_voice_probe [--pack]
                               [--game DIR] [--ffmpeg PATH]
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from xml.sax.saxutils import quoteattr

sys.path.insert(0, str(Path(__file__).resolve().parent))
from x4cat import find_game_dir, load_all  # noqa: E402

VOICE_RE = re.compile(r"^voice-(l\d+)/(\d+)/([a-z_]+)/([^/]+)\.ogg$", re.I)
ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------- 工具探测

def find_ffmpeg(explicit: str | None = None) -> str:
    """按 参数 -> PATH -> 常见 winget 安装位置 的顺序找 ffmpeg。"""
    if explicit:
        return explicit
    env = os.environ.get("FFMPEG")
    if env and Path(env).is_file():
        return env
    found = shutil.which("ffmpeg")
    if found:
        return found
    local = Path(os.environ.get("LOCALAPPDATA", ""))
    for base in (local / "Microsoft/WinGet/Packages", Path("C:/ffmpeg/bin")):
        if base.is_dir():
            for cand in base.glob("**/bin/ffmpeg.exe"):
                return str(cand)
    raise SystemExit("找不到 ffmpeg，请用 --ffmpeg 指定或加入 PATH")


# ---------------------------------------------------------------- TTS

def tts_to_wav(text: str, out_wav: Path, voice: str | None, rate: int = 0) -> None:
    """用 Windows SAPI 合成语音。通过 -EncodedCommand 传参，规避中文编码问题。"""
    ps = (
        "Add-Type -AssemblyName System.Speech; "
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        + (f'$s.SelectVoice("{voice}"); ' if voice else "")
        + f"$s.Rate = {int(rate)}; "
        + f'$s.SetOutputToWaveFile("{out_wav}"); '
        + "$s.Speak([Console]::In.ReadToEnd()); $s.Dispose()"
    )
    # -EncodedCommand 需要 UTF-16LE base64
    import base64

    encoded = base64.b64encode(ps.encode("utf-16-le")).decode("ascii")
    proc = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        input=text.encode("utf-8"),
        capture_output=True,
    )
    if proc.returncode != 0 or not out_wav.is_file():
        raise SystemExit(
            f"TTS 失败: {proc.stderr.decode('utf-8', 'replace')[:400]}\n"
            f"（可用 `powershell -c \"Add-Type -AssemblyName System.Speech; "
            f"(New-Object System.Speech.Synthesis.SpeechSynthesizer).GetInstalledVoices()\"` 查看可用声音）"
        )


def wav_to_x4_ogg(wav: Path, ogg: Path, ffmpeg: str, gain_db: float = 0.0) -> None:
    """转成 X4 语音格式：单声道 44100 Hz Vorbis。"""
    ogg.parent.mkdir(parents=True, exist_ok=True)
    filters = ["-af", f"volume={gain_db}dB"] if gain_db else []
    cmd = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(wav),
        "-ac", "1", "-ar", "44100",
        *filters,
        "-c:a", "libvorbis", "-q:a", "3",
        str(ogg),
    ]
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0 or not ogg.is_file():
        raise SystemExit(f"ffmpeg 转码失败: {proc.stderr.decode('utf-8', 'replace')[:400]}")


def tts_batch(jobs: list[tuple[str, str | None, int, Path]], workdir: Path) -> None:
    """批量合成：一次 PowerShell 进程处理全部条目（逐条启动进程太慢）。

    jobs = [(文本, 声音名, 语速, 输出 wav 路径), ...]
    """
    import base64

    manifest = workdir / "tts_manifest.txt"
    rows = [f"{out}\t{voice or ''}\t{rate}\t{text}" for text, voice, rate, out in jobs]
    manifest.write_text("\n".join(rows), encoding="utf-8")

    ps = (
        "Add-Type -AssemblyName System.Speech; "
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        f'$lines = [System.IO.File]::ReadAllLines("{manifest}", [System.Text.Encoding]::UTF8); '
        "$i = 0; "
        "foreach ($l in $lines) { "
        "  $i++; "
        '  $p = $l -split "`t", 4; '
        "  if ($p.Length -lt 4 -or -not $p[3].Trim()) { continue } "
        "  if ($p[1]) { try { $s.SelectVoice($p[1]) } catch {} } "
        "  $s.Rate = [int]$p[2]; "
        "  try { $s.SetOutputToWaveFile($p[0]); $s.Speak($p[3]) } catch { Write-Error \"line $i failed: $_\" } "
        "} "
        "$s.Dispose()"
    )
    encoded = base64.b64encode(ps.encode("utf-16-le")).decode("ascii")
    proc = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        capture_output=True,
    )
    missing = [str(out) for _t, _v, _r, out in jobs if not out.is_file()]
    if missing:
        raise SystemExit(
            f"批量 TTS 有 {len(missing)} 条未生成，首条: {missing[0]}\n"
            f"stderr: {proc.stderr.decode('utf-8', 'replace')[:400]}"
        )


def clean_tts_text(text: str, skip_pattern: str | None = None) -> str | None:
    """清理台词文本：去掉 (占位符)、*** 标记；命中 skip_pattern 则返回 None 表示跳过。"""
    if skip_pattern and re.search(skip_pattern, text, re.I):
        return None
    t = re.sub(r"\([^)]*\)", "", text)
    t = re.sub(r"\*+", "", t)
    t = html.unescape(t)
    t = re.sub(r"\s+", " ", t).strip()
    if not re.search(r"[\w\u4e00-\u9fff]", t):     # 只剩标点/空
        return None
    return t


# ---------------------------------------------------------------- 计划执行

def enumerate_targets(game: Path, langs: list[str] | None) -> dict[tuple[str, str, str, str], str]:
    """扫描归档，返回 {(lang, page, context, line): 归档路径}。"""
    index = load_all(game)
    out: dict[tuple[str, str, str, str], str] = {}
    for path in index:
        m = VOICE_RE.match(path)
        if not m:
            continue
        lang, page, context, line = m.groups()
        lang = lang.lower()
        if langs and lang not in langs:
            continue
        out[(lang, page, context, line)] = path
    return out


def assert_xml(path: Path) -> None:
    """确保产物是合法 XML —— X4 解析 content.xml 失败会静默忽略整个扩展。"""
    import xml.etree.ElementTree as ET

    try:
        ET.parse(path)
    except ET.ParseError as exc:
        raise SystemExit(f"生成的 XML 不合法，X4 会忽略这个扩展: {path}\n  {exc}")


def generate_sound_library_diff(game: Path, redirects: list[dict]) -> str:
    """生成 sound_library.xml 的 diff：把指定 sound 的 <sample start> 指向新路径。

    直接从原版 sound_library.xml 抠出原始 <sound> 定义再改写 sample，
    这样 repeat/is3d/preload/effects 等属性全部原样保留。
    """
    index = load_all(game, ["08"])
    entry = None
    for key in index:
        if key.lower() == "libraries/sound_library.xml":
            entry = index[key]
            break
    if entry is None:
        raise SystemExit("归档里找不到 libraries/sound_library.xml")
    from x4cat import read_file

    src = read_file(game, entry).decode("utf-8", errors="replace")

    parts = ['<?xml version="1.0" encoding="utf-8"?>', "<diff>"]
    for r in redirects:
        sid = r["id"]
        m = re.search(rf'<sound id="{re.escape(sid)}".*?</sound>', src, re.S)
        if not m:
            raise SystemExit(f"原版 sound_library.xml 里没有 sound id={sid}")
        block = m.group(0)
        new_sample = r["sample"]
        if "<sample " in block:
            block2, n = re.subn(r'(<sample[^>]*start=")[^"]*(")',
                                lambda mm: mm.group(1) + new_sample + mm.group(2), block, count=1)
            if n != 1:
                raise SystemExit(f"改写 {sid} 的 sample 失败")
        else:
            block2 = block.replace("</sound>", f'  <sample start="{new_sample}"/>\n  </sound>')
        parts.append(f'  <replace sel="/soundlibrary/sound[@id=\'{sid}\']">')
        parts.append("    " + block2.replace("\n", "\n    ").strip())
        parts.append("  </replace>")
    parts.append("</diff>")
    return "\n".join(parts) + "\n"


def cmd_build(args: argparse.Namespace) -> None:
    plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    game = find_game_dir(args.game)
    ffmpeg = find_ffmpeg(args.ffmpeg)

    mod_id = plan["id"]
    langs = [l.lower() for l in plan.get("langs", ["l044"])]
    out_root = Path(args.out)
    if not out_root.is_absolute():
        out_root = ROOT / out_root
    if out_root.exists() and args.clean:
        shutil.rmtree(out_root)
    out_root.mkdir(parents=True, exist_ok=True)

    available = enumerate_targets(game, langs)
    print(f"归档内匹配语音条目: {len(available)}")

    # 0) 需要原台词做 TTS 时，先载入 t 文件文本
    line_texts: dict[tuple[str, str], str] = {}
    if any(t.get("tts_text") == "@t" for t in plan["targets"]):
        from voice_inventory import parse_tfile
        from x4cat import read_file

        tlang = plan.get("text_lang", "l086")
        base = load_all(game, [plan.get("text_cat", "09")])
        tname = f"t/0001-{tlang}.xml"
        tentry = base.get(tname) or next(
            (base[k] for k in base if k.lower() == tname.lower()), None)
        if tentry is None:
            raise SystemExit(f"找不到 {tname}（用于 tts_text=@t）")
        _pages, texts = parse_tfile(read_file(game, tentry))
        for pid, d in texts.items():
            for lid, txt in d.items():
                line_texts[(pid, lid)] = txt
        print(f"  载入 {tlang} 台词 {len(line_texts)} 条")

    skip_pat = plan.get("skip_text_pattern", r"sound effect|not to be voiced")
    gain = plan.get("gain_db", 0.0)

    # 1) 解析计划 -> 目标路径集合
    #    dest 模板可用 {lang} {page} {context} {line}；缺省即原版路径 voice-<lang>/<page>/<context>/<line>.ogg
    jobs: list[tuple[Path, str]] = []          # (目标相对路径, 最终 TTS 文本)
    skipped = 0
    for t in plan["targets"]:
        page = str(t["page"])
        contexts = t.get("contexts") or ["normal", "comm", "comm_npc", "comm_broadcast"]
        want_lines = t.get("lines", "all")
        dest_tpl = t.get("dest")
        use_orig = t.get("tts_text") == "@t"
        matched = 0
        for (lang, pg, ctx, line), _src in available.items():
            if pg != page or ctx not in contexts:
                continue
            if want_lines != "all" and line not in {str(x) for x in want_lines}:
                continue
            if args.only_context and ctx != args.only_context:
                continue
            if use_orig:
                raw = line_texts.get((pg, line))
                if raw is None:
                    skipped += 1
                    continue
                text = clean_tts_text(raw, skip_pat)
                if text is None:
                    skipped += 1
                    continue
            else:
                text = t["tts_text"]
            if dest_tpl:
                rel = Path(dest_tpl.format(lang=lang, page=pg, context=ctx, line=line))
            else:
                rel = Path(f"voice-{lang}") / pg / ctx / f"{line}.ogg"
            jobs.append((rel, text))
            matched += 1
        label = "t 文件原文" if use_orig else repr(t["tts_text"])
        print(f"  page {page} {contexts}: 命中 {matched} 个文件 -> {label}")

    if not jobs:
        raise SystemExit("计划没有命中任何语音文件，检查 page/context/lines 设置")
    if skipped:
        print(f"  （跳过 {skipped} 条：无文本或命中 skip_text_pattern）")

    # 2) 按文本去重 -> 批量合成 -> 转 ogg -> 铺到目标路径
    voice = plan.get("voice", "Microsoft Huihui Desktop")
    rate = plan.get("rate", 0)
    uniq: dict[str, str] = {}                   # 文本 -> 缓存 key
    for _rel, text in jobs:
        uniq.setdefault(text, f"u{len(uniq)}")
    print(f"  唯一台词 {len(uniq)} 条，开始批量 TTS…")

    tmp = Path(tempfile.mkdtemp(prefix="x4tts_"))
    try:
        batch = [(text, voice, rate, tmp / f"{key}.wav") for text, key in uniq.items()]
        tts_batch(batch, tmp)
        ogg_of: dict[str, Path] = {}
        for text, key in uniq.items():
            ogg = tmp / f"{key}.ogg"
            wav_to_x4_ogg(tmp / f"{key}.wav", ogg, ffmpeg, gain_db=gain)
            ogg_of[text] = ogg
        print(f"  TTS + 转码完成，共 {len(ogg_of)} 个音频")
        for rel, text in jobs:
            dest = out_root / mod_id / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ogg_of[text], dest)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # 3) content.xml
    #    注意：name/description 是写进 XML 属性的用户文本，必须转义。
    #    这里曾因为 "Chinese Ship & Station ..." 里裸奔的 & 生成非法 XML，
    #    导致 X4 直接忽略整个扩展（扩展列表里根本看不到）。
    def xattr(value: object) -> str:
        return quoteattr(str(value))

    content = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        f'<content id={xattr(mod_id)} name={xattr(plan["name"])} version="100" '
        f'date={xattr(plan.get("date", "2026-01-01"))} save="0" '
        f'description={xattr(plan["description"])}>\n'
        f'  <text language="44" name={xattr(plan["name"])} '
        f'description={xattr(plan["description"])}/>\n'
        f'  <text language="86" name={xattr(plan.get("name_zh", plan["name"]))} '
        f'description={xattr(plan.get("description_zh", plan["description"]))}/>\n'
        "</content>\n"
    )
    content_path = out_root / mod_id / "content.xml"
    content_path.write_text(content, encoding="utf-8")
    assert_xml(content_path)

    # 3b) 可选的 sound_library.xml 重定向（把某个 sound 的 sample 指到别处）
    if plan.get("sound_library_redirect"):
        libdir = out_root / mod_id / "libraries"
        libdir.mkdir(parents=True, exist_ok=True)
        xml = generate_sound_library_diff(game, plan["sound_library_redirect"])
        lib_path = libdir / "sound_library.xml"
        lib_path.write_text(xml, encoding="utf-8")
        assert_xml(lib_path)
        print(f"  生成 sound_library.xml 重定向: "
              f"{[r['id'] for r in plan['sound_library_redirect']]}")

    # 3c) 可选：写入预置的 dds/xml 等附加文件
    for extra in plan.get("extra_files", []):
        dst = out_root / mod_id / extra["path"]
        dst.parent.mkdir(parents=True, exist_ok=True)
        if "text" in extra:
            dst.write_text(extra["text"], encoding="utf-8")
        else:
            shutil.copyfile(ROOT / extra["src"], dst)
        print(f"  附加文件: {extra['path']}")

    n_files = sum(1 for _ in (out_root / mod_id).rglob("*.ogg"))
    print(f"\n扩展目录: {out_root / mod_id}  ({n_files} 个 ogg)")

    # 4) 可选打包 cat/dat
    if args.pack:
        sys.argv = ["x4cat.py", "pack", "--src", str(out_root / mod_id), "--out",
                    str(out_root / mod_id / "ext_01")]
        from x4cat import main as cat_main

        cat_main()
        # 打包后把散装 ogg 收进 _loose 备查，避免体积翻倍
        if args.move_loose:
            stash = out_root / f"{mod_id}_loose"
            stash.mkdir(parents=True, exist_ok=True)
            for item in list((out_root / mod_id).iterdir()):
                if item.name.startswith("voice-"):
                    shutil.move(str(item), str(stash / item.name))
            print(f"散装语音已移到: {stash}")


def main() -> None:
    ap = argparse.ArgumentParser(description="X4 语音替换 mod 构建器")
    ap.add_argument("--plan", required=True, help="替换计划 JSON")
    ap.add_argument("--out", default="dist", help="输出根目录（相对项目根）")
    ap.add_argument("--game", help="游戏目录（默认自动探测）")
    ap.add_argument("--ffmpeg", help="ffmpeg 路径（默认 PATH/常见位置探测）")
    ap.add_argument("--pack", action="store_true", help="同时打包成 ext_01.cat/dat")
    ap.add_argument("--move-loose", action="store_true", help="打包后把散装语音移出扩展目录")
    ap.add_argument("--clean", action="store_true", help="构建前清空输出目录")
    ap.add_argument("--only-context", help="只生成指定 context（便于快速验证）")
    ap.set_defaults(func=cmd_build)
    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
