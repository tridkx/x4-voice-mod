# 中文飞船与空间站播报 v2.0（火山引擎配音）

把《X4：基石》里的**飞船电脑**和**空间站公共广播**换成中文语音。

> v1.0 用的是 Windows 系统语音（SAPI），这一版换成**火山引擎豆包 TTS**，并做了文本改写。

## 音色

| 用途 | 音色 | voice_type | 参数 |
|---|---|---|---|
| 飞船电脑 Betty | **湾湾小何**（TTS 1.0） | `zh_female_wanwanxiaohe_moon_bigtts` | 指令「用略带俏皮和活力的语气播报，不要一板一眼」+ 语速 +2 |
| 空间站公共广播 | **温柔妈妈 2.0** | `zh_female_wenroumama_uranus_bigtts` | 无指令 |

## 覆盖范围

| 内容 | page | 台词数 | 音频文件 |
|---|---|---|---|
| 飞船电脑 Betty（「注意。」「护盾危急。」「自动驾驶已激活」…） | `10002` | 504 | 1008 |
| 空间站公共广播（「警卫提醒所有游客…」「注意！空间站正在遭受攻击…」） | `10099` | 73 | 73 |

合计 **1081 个音频**，全部 **Ogg Vorbis / 单声道 / 44100 Hz**，与原版格式一致。

## 安装

1. 解压，把 `x4_annc_cn` 文件夹整个放进 `X4 Foundations\extensions\`
2. 启动游戏，在**扩展 / Extensions** 里确认「中文飞船与空间站播报（火山配音）」已启用
3. **重启游戏**（扩展的归档只在启动时挂载）

**不需要任何启动参数，不修改任何原版文件。** 卸载就是删掉这一个文件夹，原版语音毫发无损。

## 文本改写

飞船与空间站共 577 条台词里，改写了 **26 条**，分两类：

- **A 类 · 真语病（7 条）**：如「有人呼叫我们」→「收到呼叫请求」；
  「请不要空间站的监控摄像头前摆pose」→ 补一个「在」字
- **B 类 · 翻译腔 / 生硬 / 错别字（19 条）**：如「极其失败。」→「彻底失败。」；
  「栓牢」→「拴牢」、「报道」→「报到」

**保持原样的**：拼接零件（「来自」「。重复——」「由舱口」—— 原是拼长句的零件，改了会破坏拼接）、
中英混杂术语（pose / Terran / Xenon / Argon / EGOSOFT / AI核心Mk7）。

完整对照表见仓库的 `docs/改写对照表.md`。

## 实现方式

语音的加载位置由 `libraries/sound_library.xml` 里的 `<sample start="...">` 决定。
本 mod 用 `<diff><replace>` 把 5 条 sound 的 sample 指向扩展内路径：

```
10002_normal           -> extensions\x4_annc_cn\voice\10002\normal
10002_comm             -> extensions\x4_annc_cn\voice\10002\normal
10002_comm_broadcast   -> extensions\x4_annc_cn\voice\10002\comm
10099_normal           -> extensions\x4_annc_cn\voice\10099\station_broadcast
10099_comm_broadcast   -> extensions\x4_annc_cn\voice\10099\station_broadcast
```

**注意**：直接往扩展里放同名 `voice-l044/...` 去覆盖原版是**无效的**（实测确认）——
覆盖机制对脚本/贴图/UI 有效，对音频这条查找路径无效。必须走重定向。
