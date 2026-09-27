#!/usr/bin/env python3
"""火山引擎 TTS 版语音 mod 构建器（飞船=湾湾小何 1.0 / 空间站=温柔妈妈 2.0）。

与 voice_mod_builder.py 的区别：TTS 后端从 Windows SAPI 换成火山豆包，
且两代音色走不同的 X-Api-Resource-Id 通道。

    rid: seed-tts-1.0  → _moon_bigtts 系列（1.0 音色，如湾湾小何）
    rid: seed-tts-2.0  → _uranus_bigtts 系列（2.0 音色，如温柔妈妈）
    （实测用 volc.service_type.10029 调 1.0 音色虽能出声，但 context_texts 会被忽略）

用法：
    VOLC_TTS_KEY=xxx python build_volc_mod.py --plan plans/volc_cn.json --out dist/x4_annc_cn
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import io
import shutil
import subprocess
import sys
import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
from x4cat import find_game_dir, load_all, read_file  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
ENDPOINT = "https://openspeech.bytedance.com/api/v3/tts/unidirectional/sse"
VOICE_RE = re.compile(r"^voice-(l\d+)/(\d+)/([a-z_]+)/([^/]+)\.ogg$", re.I)


# ─────────────────────────────────────────────────────── 文本改写（已审定）

# A 类：真语病，必须改（中英混杂的常用短语与种族名一律保留，如 pose / Terran / Xenon）
FIX_A = {
    "10002": {
        "20":  "收到呼叫请求",                       # 有人呼叫我们
        "272": "等级提升。",                         # 你升级了。
        "273": "等级下降。",                         # 你降级了。
        "279": "收到汇款",                           # 收到转入资金
        "414": "彻底失败。",                         # 极其失败。
    },
    "10099": {
        # 请不要(缺"在")空间站的监控摄像头前摆pose
        "1020": "请不要在空间站的监控摄像头前摆pose，这会让安监人员漏掉某些犯罪行为。",
        # 由于…活动已被取消（缺谓语）
        "3049": "由于航线上出现Xenon活动，本次航班已取消。请所有乘客与接待处联系。",
    },
}

# B 类：翻译腔 / 生硬 / 错别字，按建议改写
FIX_B = {
    "10002": {
        "140":     "需要进一步削弱目标护盾。",        # 目标的护盾必须要再降低一些。
        "142":     "无法登陆。",                      # 登陆是不可能的。
        "205":     "数据库中无此条目。",              # 未在数据库中找到条目。
        "257":     "你的声望不足。",                  # 你的名声太低了。
        "516":     "检测到异常物体。",                # 检测到不规则的事物。
        "707":     "这个演示将在倒计时 1 分钟后超时",  # 这个演示将在T减1分钟后超时
        "1512":    "有多种伪装可用。",                # 有多个派系伪装可用。
        "30224528": "签名伪装正在失效。",              # 签名伪装失效中。
        "30224630": "安保条件不符。",                 # 安保状况不适。
        "30224652": "全力运算中……",                  # 正在全力计算。
        "30224664": "受影响的开关：",                 # 受影响开关：
        "30224707": "正在与黑泽神建立长距离上行链路。", # 正在和黑泽神建立长距离上行链。
        "30284004": "反馈已转达。",                   # 反馈已转发。
        "30284012": "事件回放。",                     # 还原事件。
    },
    "10099": {
        "1005": "如果你带了动物上船，请确保拴牢它们。任何未被拴住的东西都会被当场汽化。",  # 栓→拴
        "1006": "本工厂有乞丐正在乞讨，请勿支持这些职业乞丐。如果您有多余的钱，请捐给正规的慈善机构。",
        "1010": "我们接到报告说这个星区爆发了Argon流感，请全体游客到医疗处报到以便接种疫苗。",  # 报道→报到
        "1014": "EGOSOFT感谢您购买本产品。",           # …购买他们的产品。
        "1016": "请记得把废弃的能量块回收处理。",        # 做循环处理
    },
}


def rewrite(page: str, line: str, text: str) -> tuple[str, str | None]:
    """返回 (新文本, 改写类别)。类别为 None 表示未改动。"""
    if line in FIX_A.get(page, {}):
        return FIX_A[page][line], "A"
    if line in FIX_B.get(page, {}):
        return FIX_B[page][line], "B"
    return text, None


# ─────────────────────────────────────────────────────── 火山 TTS

def volc_tts(text: str, speaker: str, resource_id: str, api_key: str,
             context: str | None = None, speech_rate: int = 0,
             sample_rate: int = 44100, retries: int = 3) -> bytes:
    """调火山单向流式 TTS，返回 mp3 字节。"""
    headers = {
        "Content-Type": "application/json",
        "X-Api-Key": api_key,
        "X-Api-Resource-Id": resource_id,
        "X-Api-Request-Id": str(uuid.uuid4()),
    }
    additions: dict = {"disable_markdown_filter": True}
    if context:
        additions["context_texts"] = [context]
    body = {
        "user": {"uid": "x4voicemod"},
        "req_params": {
            "text": text,
            "speaker": speaker,
            "sample_rate": sample_rate,
            "audio_params": {"format": "mp3", "speech_rate": speech_rate, "bit_rate": 128000},
            "additions": json.dumps(additions, ensure_ascii=False),
        },
    }
    last = ""
    for attempt in range(retries):
        try:
            chunks: list[bytes] = []
            with httpx.Client(timeout=90) as client:
                with client.stream("POST", ENDPOINT, headers=headers, json=body) as resp:
                    if resp.status_code != 200:
                        last = f"HTTP {resp.status_code}: {resp.read()[:160]}"
                        continue
                    for line in resp.iter_lines():
                        if not line.startswith("data:"):
                            continue
                        try:
                            d = json.loads(line[5:].strip())
                        except json.JSONDecodeError:
                            continue
                        code = d.get("code", 0)
                        if code not in (0, 20000000):
                            last = f"code={code} {d.get('message', '')[:100]}"
                            break
                        if d.get("data"):
                            chunks.append(base64.b64decode(d["data"]))
            if chunks:
                return b"".join(chunks)
        except Exception as exc:                      # 网络抖动重试
            last = f"{type(exc).__name__}: {str(exc)[:100]}"
    raise RuntimeError(f"TTS 失败（{retries} 次）: {last}")


def mp3_to_x4_ogg(mp3: Path, ogg: Path, ffmpeg: str) -> None:
    """转成 X4 语音格式：单声道 Vorbis / 44100 Hz。"""
    ogg.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(mp3),
         "-ac", "1", "-ar", "44100", "-c:a", "libvorbis", "-q:a", "3", str(ogg)],
        capture_output=True,
    )
    if proc.returncode != 0 or not ogg.is_file():
        raise RuntimeError(f"ffmpeg 转码失败: {proc.stderr.decode('utf-8', 'replace')[:200]}")


def trim_silence(ogg: Path, head: float = 0.03, tail: float = 0.06, thr_db: float = -45) -> bool:
    """裁掉音频首尾静音，原地替换。

    火山 TTS 每条输出都带约 174ms 首静音 + 500ms 尾静音。完整句里这只是浪费，
    但 X4 的飞船电脑语音是「名词零件 + 状态零件」拼起来的（441 自动驾驶 + 403 启动），
    两个静音一叠加就凭空多出 685ms 空白，听感直接从「自动驾驶，启动」变成
    「自动驾驶…………启动」。裁到 30ms 头 / 60ms 尾后空隙降到约 106ms。
    """
    import wave
    import uuid as _uuid
    import numpy as np

    def decode(path: Path):
        r = subprocess.run(["ffmpeg", "-v", "quiet", "-i", str(path), "-f", "wav",
                            "-ac", "1", "-ar", "44100", "-"], capture_output=True)
        with wave.open(io.BytesIO(r.stdout)) as w:
            return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16), w.getframerate()

    x, sr = decode(ogg)
    loud = np.where(np.abs(x) > 32768 * (10 ** (thr_db / 20)))[0]
    if len(loud) == 0:
        return False
    a = max(0, loud[0] - int(head * sr))
    b = min(len(x), loud[-1] + int(tail * sr))
    if b - a < int(0.15 * sr) or (b - a) >= len(x) - int(0.01 * sr):
        return False
    tmp = ogg.with_name(f"_t{_uuid.uuid4().hex[:6]}.ogg")
    r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(ogg), "-ss", f"{a/sr:.3f}",
                        "-t", f"{(b-a)/sr:.3f}", "-ac", "1", "-ar", "44100",
                        "-c:a", "libvorbis", "-q:a", "3", str(tmp)], capture_output=True)
    if r.returncode == 0 and tmp.is_file() and tmp.stat().st_size > 1000:
        os.replace(tmp, ogg)
        return True
    tmp.unlink(missing_ok=True)
    return False


def find_ffmpeg(explicit: str | None = None) -> str:
    if explicit:
        return explicit
    found = shutil.which("ffmpeg")
    if found:
        return found
    local = Path(os.environ.get("LOCALAPPDATA", ""))
    for cand in (local / "Microsoft/WinGet/Packages").glob("**/bin/ffmpeg.exe"):
        return str(cand)
    raise SystemExit("找不到 ffmpeg")


# ─────────────────────────────────────────────────────── sound_library diff

def sound_library_diff(game: Path, redirects: list[dict]) -> str:
    index = load_all(game, ["08"])
    entry = next((index[k] for k in index if k.lower() == "libraries/sound_library.xml"), None)
    if entry is None:
        raise SystemExit("归档里找不到 libraries/sound_library.xml")
    src = read_file(game, entry).decode("utf-8", errors="replace")
    parts = ['<?xml version="1.0" encoding="utf-8"?>', "<diff>"]
    for r in redirects:
        sid = r["id"]
        m = re.search(rf'<sound id="{re.escape(sid)}".*?</sound>', src, re.S)
        if not m:
            raise SystemExit(f"原版 sound_library.xml 里没有 sound id={sid}")
        block = re.sub(r'(<sample[^>]*start=")[^"]*(")',
                       lambda mm: mm.group(1) + r["sample"] + mm.group(2), m.group(0), count=1)
        parts.append(f'  <replace sel="/soundlibrary/sound[@id=\'{sid}\']">')
        parts.append("    " + block.replace("\n", "\n    ").strip())
        parts.append("  </replace>")
    parts.append("</diff>")
    return "\n".join(parts) + "\n"


# ─────────────────────────────────────────────────────── 主流程

def main() -> None:
    ap = argparse.ArgumentParser(description="火山 TTS 语音 mod 构建器")
    ap.add_argument("--plan", required=True)
    ap.add_argument("--out", default="dist/x4_annc_cn")
    ap.add_argument("--game")
    ap.add_argument("--ffmpeg")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--limit", type=int, help="只做前 N 条（调试用）")
    ap.add_argument("--pack", action="store_true", help="打包成 ext_01.cat/dat")
    args = ap.parse_args()

    api_key = os.environ.get("VOLC_TTS_KEY", "").strip()
    if not api_key:
        raise SystemExit("需要环境变量 VOLC_TTS_KEY")
    plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    game = find_game_dir(args.game)
    ffmpeg = find_ffmpeg(args.ffmpeg)

    out_root = Path(args.out)
    if not out_root.is_absolute():
        out_root = ROOT / out_root
    stage = out_root / plan["id"]
    stage.mkdir(parents=True, exist_ok=True)

    # 1) 收集要合成的目标路径（按归档里实际存在的条目）
    #    注意：load_all 的第二个参数筛的是「归档名」（03 / ego_dlc_boron），不是语言 ——
    #    语言必须在 VOICE_RE 匹配之后自己筛。
    want_langs = {l.lower() for l in plan.get("langs", ["l044"])}
    idx = load_all(game)
    want: list[tuple[str, str, str, str]] = []          # (lang, page, context, line)
    for path in idx:
        m = VOICE_RE.match(path)
        if m:
            lang, page, ctx, line = m.groups()
            if lang.lower() in want_langs:
                want.append((lang.lower(), page, ctx, line))
    print(f"归档内 voice 条目 {len(want)} 个（语言 {sorted(want_langs)}）")
    groups = plan["groups"]
    tasks: list[dict] = []
    for lang, page, ctx, line in want:
        for g in groups:
            if page == str(g["page"]) and ctx in g["contexts"]:
                tasks.append({"lang": lang, "page": page, "ctx": ctx, "line": line, **g,
                              "dest": g["dest"].format(lang=lang, page=page, context=ctx, line=line)})
    if args.limit:
        tasks = tasks[: args.limit]
    print(f"待合成 {len(tasks)} 个目标文件，涉及 {len({t['line'] for t in tasks})} 条唯一台词")

    # 2) 改写文本（同一条台词只改一次，normal/comm 共用）
    text_map = json.loads(Path(plan["text_source"]).read_text(encoding="utf-8"))
    changes: list[tuple[str, str, str, str, str]] = []
    for t in tasks:
        orig = text_map.get(f"{t['page']}/{t['line']}")
        if orig is None:
            t["text"] = None
            continue
        new, kind = rewrite(t["page"], t["line"], orig)
        t["text"] = new
        if kind:
            changes.append((kind, t["page"], t["line"], orig, new))
    missing = sum(1 for t in tasks if not t["text"])
    print(f"改写 {len(changes)} 条（A 类 {sum(1 for c in changes if c[0]=='A')} / "
          f"B 类 {sum(1 for c in changes if c[0]=='B')}），无文本跳过 {missing} 条")

    # 3) 并发合成唯一台词
    uniq: dict[tuple[str, str], str] = {}               # (page, text) -> 用于缓存
    for t in tasks:
        if t["text"]:
            uniq.setdefault((t["page"], t["text"]), t["text"])
    print(f"唯一台词 {len(uniq)} 条，开始并发合成（{args.workers} 并发）…")

    tmp = Path(tempfile.mkdtemp(prefix="volctts_"))
    audio_cache: dict[tuple[str, str], Path] = {}
    done = 0

    def work(item: tuple[tuple[str, str], str]) -> tuple[tuple[str, str], Path]:
        (page, text), _ = item
        g = next(x for x in groups if str(x["page"]) == page)
        mp3 = tmp / f"{uuid.uuid4().hex}.mp3"
        ogg = mp3.with_suffix(".ogg")
        mp3.write_bytes(volc_tts(text, g["speaker"], g["resource_id"], api_key,
                                 context=g.get("context"), speech_rate=g.get("speech_rate", 0)))
        mp3_to_x4_ogg(mp3, ogg, ffmpeg)
        trim_silence(ogg)                     # 去掉火山 TTS 的首尾静音
        mp3.unlink(missing_ok=True)
        return (page, text), ogg

    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(work, it): it for it in uniq.items()}
        for fut in as_completed(futures):
            try:
                key, ogg = fut.result()
                audio_cache[key] = ogg
                done += 1
                if done % 50 == 0:
                    print(f"  已完成 {done}/{len(uniq)}")
            except Exception as exc:
                errors.append(f"{futures[fut][0][0]}/{futures[fut][0][1][:18]}: {exc}")
    print(f"合成完成 {done}/{len(uniq)}，失败 {len(errors)}")
    for e in errors[:8]:
        print("   FAIL", e)

    # 4) 铺到扩展目录
    for t in tasks:
        if not t["text"]:
            continue
        src = audio_cache.get((t["page"], t["text"]))
        if src is None:
            continue
        dst = stage / t["dest"]
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
    shutil.rmtree(tmp, ignore_errors=True)
    n_ogg = sum(1 for _ in stage.rglob("*.ogg"))
    print(f"扩展目录 {stage}：{n_ogg} 个 ogg")

    # 5) content.xml + sound_library 重定向
    content = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        f'<content id="{plan["id"]}" name="{plan["name"]}" version="200" date="{plan["date"]}" '
        f'save="0" description="{plan["description"]}">\n'
        f'  <text language="44" name="{plan["name"]}" description="{plan["description"]}"/>\n'
        f'  <text language="86" name="{plan["name_zh"]}" description="{plan["description_zh"]}"/>\n'
        "</content>\n"
    )
    (stage / "content.xml").write_text(content, encoding="utf-8")
    libdir = stage / "libraries"
    libdir.mkdir(parents=True, exist_ok=True)
    (libdir / "sound_library.xml").write_text(
        sound_library_diff(game, plan["sound_library_redirect"]), encoding="utf-8")

    # 6) 改写对照表
    report = ROOT / "docs" / "改写对照表.md"
    lines = ["# 文本改写对照表", "",
             f"共 {len(changes)} 条被改写（A 类真语病 {sum(1 for c in changes if c[0]=='A')} 条，"
             f"B 类翻译腔/生硬 {sum(1 for c in changes if c[0]=='B')} 条）。",
             "拼接零件（如「来自」「。重复——」）与中英混杂术语（pose / Terran / Xenon）保持原样。", "",
             "| 类 | page | line | 原文 | 改写后 |", "|---|---|---|---|---|"]
    for kind, page, line, old, new in sorted(changes):
        lines.append(f"| {kind} | {page} | `{line}` | {old} | {new} |")
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"改写对照表 -> {report}")

    if args.pack:
        subprocess.run([sys.executable, str(ROOT / "tools" / "x4cat.py"), "pack",
                        "--src", str(stage), "--out", str(out_root / "ext_01"),
                        "--exclude", "content.xml"], check=True)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
