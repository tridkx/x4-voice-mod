# X4: Foundations 语音替换 mod 工程

把《X4：基石》的游戏内语音换成自定义音频（中文配音 / 换角色音色 / 单句替换）。

游戏版本：9.00（`version.dat = 900`），语言：中文界面（`lang.dat = 86`），语音仅英语 `l044` / 德语 `l049` 两套。

**状态：技术路线已跑通，成品已实机验证。** 当前交付一个把**飞船电脑与空间站广播**换成中文的扩展。

## 成品与安装

| 内容 | page | 台词数 |
|---|---|---|
| 飞船电脑 Betty（「自动驾驶已激活」「护盾危急」…） | `10002` | 504 |
| 空间站公共广播（「警卫提醒所有游客…」） | `10099` | 73 |

把 `dist/x4_annc_cn/` 整个目录复制到游戏的 `extensions/` 下，重启游戏即可。
**不需要任何启动参数，不修改任何原版文件，卸载就是删掉这一个目录。**

## 一句话结论

**语音的加载位置由 `libraries/sound_library.xml` 里的 `<sample start="...">` 决定**，
所以做法是：把采样指向 `extensions\<mod>\voice\...`，音频放那里 —— 而不是去覆盖原版的
`voice-l044/...`（**实测那样无效**，尽管覆盖对脚本/贴图/UI 是有效的）。

玩法细节、完整证据链与四条路线的实测结论见下文。

---

## 一、语音系统的真实结构（全部实测得出）

### 1. 归档格式比想象的简单

X4 的 `.cat` **就是纯文本索引**，不是二进制：

```
assets/characters/animations/2.xsm 111354 1208774952 380e1d7ea9d0e38fa17e7e708a01ef28
└─ 相对路径 ─────────────────────┘ └大小─┘ └─mtime──┘ └────── 内容 MD5 ──────┘
```

`.dat` 里所有文件按 `.cat` 的行顺序**首尾相接**，无对齐、无压缩，因此

```
offset(i) = size(0) + size(1) + ... + size(i-1)
```

已用 `md5(读出的字节) == 索引第 4 列` 逐条验证通过。所以**不需要 XRCatTool**，自带
`tools/x4cat.py` 就能 list / extract / pack。

### 2. 语音文件的命名规则

```
voice-l<语言>/<page>/<context>/<line>.ogg      ← 音频（单声道 Vorbis 44100Hz，约 48kbps）
voice-l<语言>/<page>/lipsync/<line>.xpm        ← 口型数据（EMotion FX 生成）
```

| 字段 | 含义 | 例子 |
|---|---|---|
| `l044` / `l049` | 语音语言（英语 / 德语）。**没有中文 `l086`** | 引擎用格式串 `-l%03d` 在运行时拼出来 |
| `page` | **说话人 / 角色音色库**，与 `t/0001-l0XX.xml` 的 `<page id>` 一一对应 | `10002` = Ship Computer - Betty |
| `context` | 播放场合：`normal` `comm` `comm_npc` `comm_broadcast` `station_broadcast` | |
| `line` | 具体台词，对应 t 文件里的 `<t id>` | `11201` |

规模：**229 个说话人 page，37 203 条唯一台词**，英语 `.ogg` 144 499 个。清单见
`docs/voice_pages.csv`（全部 page）与 `work/voice_inventory.csv`（逐条台词 + 中文原文）。

### 3. 引擎怎么找到音频：`sound_library.xml` 是权威

`libraries/sound_library.xml`（在 `08.cat`）里每个语音场合登记一条：

```xml
<sound id="10002_normal" description="Board computer - Betty" repeat="1" is3d="1" preload="0">
  <sample start="voice\10002\normal"/>     ← sample 只给「目录」，不给文件名
  <effects><reverb room="sewer pipe" .../></effects>
</sound>
```

播放 `<speak page="10002" line="1" context="normal"/>` 时：

1. 按 `<page>_<context>` 查 sound id → `10002_normal`
2. 取 `sample start` = `voice\10002\normal`
3. 加语音语言后缀（`voice` → `voice-l044`）、拼上 `line` 与 `.ogg`
4. 得到真正的文件 `voice-l044/10002/normal/1.ogg`

**关键证据**：`20005_normal` 与 `20005_comm` 两条 sound 的 sample **都**指向
`voice\20005\normal` —— 说明目录由 XML 决定、不由 sound id 硬编码。这条给了「改 sample
来重定向语音」的理论依据（见下面的方案 B）。

### 4. 已确认的限制

社区 PoC（[v1024_poc_voicelines](https://github.com/Vectorial1024/v1024_poc_voicelines)，
X4 v5.0 时代）给出的结论是：**把语音文件当普通 extensions 散装文件放进去，覆盖不生效**，
必须装到游戏根目录并加启动参数 `-prefersinglefiles`。X4.exe 里确实能找到
`prefersinglefiles`、`voicelanguage` 两个参数字符串。

> 该结论只验证了「散装文件覆盖 `voice-l044`」这一条路，**没有**验证
> 「打包成 `ext_01.cat/dat`」和「改 sample 指向扩展内路径」。本工程正在实测这两条。

---

## 二、四条候选技术路线

| 路线 | 做法 | 需要什么 | 实测状态 |
|---|---|---|---|
| **A. 直接覆盖** | 扩展里放 `voice-l044/<page>/<context>/<line>.ogg`，路径与原版同名 | `ext_01.cat/dat` | ❌ **已证伪**：Betty 仍是英文（扩展确实被加载了，启用表里 `enabled="true"`） |
| **B. sample 重定向** | 改 `sound_library.xml`，把 `<sample start>` 指到 `extensions\<mod>\voice\...` | XML diff + 扩展内文件 | ✅ **已实测生效，选定方案**（Betty 说出了「扩展通道生效」） |
| **C. 根目录散装** | 音频放游戏根目录 `voice-l044/...` | 启动参数 `-prefersinglefiles` | 未测（B 成功后此路已不需要，探针已清理） |
| **D. 新增归档** | 游戏根目录放 `10.cat` + `10.dat`，编号排在基础归档之后 ⇒ 后加载 ⇒ 覆盖 | 无 | 未测（B 成功后此路已不需要，探针已清理） |
| **E. 改基础归档** | 原地重写 `03.dat` 里的语音字节 | 无 | 兜底方案，脚本 `tools/patch_base_archive.py` 已就绪 |

### 关键证据链

- **扩展能覆盖基础文件**：`starwarsmod_m1` 用 `subst_01.cat` 盖掉 13 张原版纹理、
  `ext_01.cat` 盖掉 589 个基础文件（含二进制 `.xpl`），工作正常。
  所以"扩展不能覆盖"这个说法**只对音频成立** —— 说明音频走了另一套文件查找。
- **X4 扫描 `*.cat` 并逐个挂载**：X4.exe 里有 `CatalogList::AddNewCatalog()`、通配符 `*.cat`，
  以及 `Loading %s %s.cat without a corresponding signature catalog (missing ...)`
  —— 没有 `_sig.cat` 只是**警告**，不拒绝加载。这是路线 D 的依据。
- **`sample` 是否决定路径存疑**：`20005_normal` 与 `20005_comm` 两条 sound 的 sample
  **都**写着 `voice\20005\normal`，但同一句台词在 `normal/` 与 `comm/` 目录下的文件
  **md5 全不相同**（各 context 都是独立音频版本）。若 sample 是权威，那些 `comm/` 文件就全是死文件，
  X4 不太可能白打包几百 MB —— 所以"引擎按 `<page>_<context>` 自行拼路径、sample 仅供参考"
  的可能性很大，那样路线 B 就不会生效。
- **原地改字节可行但装不下**：归档 offset 是 size 累加，所以新文件必须与原文件**等长**才能原地写。
  实测中文 TTS 比英文原版大 9–22%，10002 只有 30% 的条目塞得进去，因此路线 E 需要**重建整个
  `03.dat`**（6.1 GB）而不是原地覆盖。

---

## 三、工具链

| 工具 | 用途 |
|---|---|
| `tools/x4cat.py` | cat/dat 归档：`info` / `list` / `extract` / `pack` |
| `tools/voice_inventory.py` | 扫描归档 + 交叉 t 文件，产出语音清单 CSV |
| `tools/voice_mod_builder.py` | 读替换计划 JSON，批量 TTS 合成 → 转 X4 格式 ogg → 生成扩展 + 打包 |
| `tools/check_extensions.py` | 扩展体检：content.xml 合法性、cat/dat 配对、索引与 dat 大小是否一致 |
| `tools/patch_base_archive.py` | 原地替换基础归档里的文件（兜底路线 E）+ 回滚 |

依赖：Python 3、`ffmpeg`（转 Ogg Vorbis）、Windows SAPI（`Microsoft Huihui Desktop` 中文女声、
`Microsoft Zira Desktop` 英文女声）。游戏目录通过 `--game` 或环境变量 `X4_GAME_DIR` 指定，
不写死在代码里。

替换计划示例（`plans/` 下）：

```json
{
  "id": "x4_my_voice",
  "langs": ["l044"],
  "sound_library_redirect": [
    {"id": "10002_normal", "sample": "extensions\\x4_my_voice\\voice\\10002\\normal"}
  ],
  "targets": [
    {"page": "10002", "contexts": ["normal"], "lines": "all",
     "dest": "voice/10002/{context}/{line}.ogg",
     "tts_text": "警告，护盾能量不足", "voice": "Microsoft Huihui Desktop", "rate": 1}
  ]
}
```

`lines` 可为 `"all"` 或具体 id 列表；`dest` 用 `{lang} {page} {context} {line}` 占位符，
缺省即原版路径。

---

### 5. 播报类语音（飞船 / 空间站 / 名称）—— 本次验证的目标

Betty 是 X 系列里**飞船电脑语音**的昵称（page 10002 的标题就叫 `Ship Computer - Betty`），
她说的是「注意。」「警告！」「护盾危急。」「船体受损。」「自动驾驶已激活」这类提示音，
是玩家全程听得最多的一条音轨。注意她**不是 NPC**，是电脑音。

| page | 内容 | 唯一台词 | 实际使用的目录（由 sound_library 决定） |
|---|---|---|---|
| `10002` | **飞船电脑 Betty** | 514 | `voice\10002\normal`、`voice\10002\comm` |
| `10099` | **空间站公共广播**（Station Control） | 73 | `voice\10099\station_broadcast` |
| `20005` | 星系名播报（Location Name Pool） | 392 | `voice\20005\normal` |
| `20101` | 船名播报（Ships） | 426 | `voice\20101\normal` |
| `20102` | 空间站名播报（Stations） | 147 | `voice\20102\normal` |
| `10003` | Major Domo（另一台电脑音） | 182 | `voice\10003\normal` |

> page 目录下还有 `comm_npc`、`comm_broadcast` 等副本，但 **sound_library 里没有引用它们**
> —— 真正决定加载位置的是 `<sample start>`，改那里才有用。

## 四、当前状态

- [x] 归档格式逆清楚，读写/打包工具可用（已 round-trip 校验）
- [x] 语音系统结构、page↔角色对应、sound_library 机制摸清
- [x] 全量语音清单（229 page / 37 203 条台词 + 中文原文）
- [x] 构建流水线（t 文件原文 → 批量 TTS → ogg → 扩展 → cat/dat）跑通，557 条台词 40 秒
- [x] **路线 A 已证伪**（扩展覆盖语音无效）
- [x] **路线 B 实测生效** —— 正式中文播报包已部署
- [ ] 按需扩大覆盖范围（星系名 / 船名 / 空间站名播报，或全量 229 个 page）
- [ ] 按实测结果做正式替换工程

已产出的探针：`dist/x4_annc_cn/`（20 MB，飞船电脑 1008 个文件走路线 A、
空间站广播 146 个文件走路线 B，全部是中文 TTS 念中文原文）。

## 五、已踩过的坑（都会让 mod「装进去但在游戏里找不到」）

### 1. `content.xml` 里裸奔的 `&` 会让整个扩展被静默丢弃

第一版探针的 `name="Chinese Ship & Station Announcements"` 没有把 `&` 转义成 `&amp;`，
于是 `content.xml` 不是合法 XML。**X4 解析失败时不给任何提示，直接把整个扩展当成不存在**
—— 游戏主菜单的扩展列表里根本不会出现它。

构建器现在所有写进 XML 属性的文本都走 `quoteattr()`，并且生成后立刻用
`ElementTree` 自检，不合法就直接报错终止。

### 2. 扩展还必须在「启用状态表」里登记

```
文档\Egosoft\X4\<steamid>\content.xml
```

```xml
<content>
  <extension id="x4_rose_mod" enabled="true"/>
  <extension id="x4_lumine_mod" enabled="false"/>
</content>
```

这是 X4 的扩展启用清单，**只有被成功识别、且 `enabled="true"` 的扩展才会真正加载**。
游戏每次启动都会刷新它；新识别到的扩展按默认策略登记。排查「装好了却没效果」时
要先看这里有没有对应 id、是不是 `false`。

### 3. 体检脚本

```bash
python tools/check_extensions.py            # 扫描全部扩展，列出真正有问题和仅需注意的
python tools/check_extensions.py --ext x4_annc_cn --deep   # 单个扩展 + 抽检 dat 内容可读性
```

检查项：content.xml 是否存在且合法、根元素是否 `<content>`、`.cat/.dat` 是否成对、
**索引 size 合计是否等于 dat 实际大小**（不等说明两者不匹配，读取会错位）等。
注意 `id` 与目录名不一致是**普遍现象**（`ImprovedKhaak` 的 id 就叫 `IK`），只作提示。

## 六、实测之后的工程量提示

- **换某个角色**（如 Betty、Dal Busta）：几百到 2000 条，可行。
- **全语音中文化**：37 203 条 × 4 个 context ≈ 14 万条音频。TTS 批量跑没问题，
  但**对话时长与口型（.xpm）不匹配**会成为主要问题，需要逐条对齐或接受口型不同步。
- **加一门原版没有的语音语言**（如 `voice-l081` + 启动参数 `-voicelanguage 81`）：
  可行但必须**一次性提供全套**，缺的台词会静音而不是回落到英语。
