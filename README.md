# 轻轻的飘 · 人间体

把 QQ 群友「轻轻的飘」（天凤九段）做成两样东西：

1. **一个 Skill**（`SKILL.md` + 参考资料）：从真实聊天记录统计出来的说话风格、口癖、常用短句、真实对话片段，加上你手写的人设。可以直接放进 Claude Code / Claude 使用。
2. **一个“人间体”QQ 机器人**：挂在 NapCat 上，用这个 Skill 在群里像本人一样接话；遇到何切题会先用程序算牌效再用飘的口吻回答；可选用 GPT-SoVITS 合成的本人声音发语音。

```
聊天记录(.txt/.json/.csv) ──piao build──▶ data/work/ ──▶ skill/qingqingdepiao/
                                          messages.jsonl      SKILL.md
                                          pairs.jsonl         references/style.md
                                          profile.json        references/examples.md
                                                                   │
QQ 群 ◀──▶ NapCat (OneBot v11) ◀──▶ piao bot ──▶ Claude ◀──────────┘
                                       │  ├─ 相似场景检索（飘以前在类似话题下怎么说）
                                       │  └─ 牌效计算（消息里有 13/14 张手牌时）
                                       └─▶ GPT-SoVITS（可选，发语音）

QQ 语音(.amr/SILK) ─decode─┐
直播录像 ─extract─▶ MSST 分离降噪 ─▶ 切片 ─▶ 声纹过滤 ─▶ 转写 ─▶ GPT-SoVITS 训练集
```

## 开始之前

这是在复刻一个真实的人，请先做到这几点：

- **征得飘本人同意**，尤其是声音克隆。用别人的声音做合成需要本人同意，合成的语音也应当能被识别出来是合成的。
- **机器人用单独的小号**，群名片写明是 AI 分身（比如「飘(AI)」），并告诉群友。
- 默认开启 `self_disclose`：有人认真问“你是不是真人”时，分身会承认自己是 AI。分身也不会替本人做借钱、转账、约线下这类现实承诺，不会说出聊天记录里的隐私。
- 聊天记录、语音、生成的 Skill 都含个人信息，`.gitignore` 已经把它们排除在仓库外，别手动提交。

## 安装

需要 Python 3.11+ 和 ffmpeg。

```bash
pip install -e .            # 核心：解析、Skill、机器人
pip install -e ".[voice]"   # 语音：SILK 编解码、切片
cp config.example.toml config.toml
export ANTHROPIC_API_KEY=...  # Claude API 密钥
```

## 第一步：导出聊天记录

把导出的文件放进 `data/raw/chat/`（可以放多个群、多个文件）。机器人在主群 829124409 说话，所以最重要的是主群的完整记录：群友的称呼、梗和飘在这个群里的说话方式都从这里学。支持：

| 来源 | 格式 |
| --- | --- |
| 旧版 PC QQ「消息管理器 → 导出为 txt」 | `.txt`，自动识别 `2021-03-04 21:15:32 昵称(QQ号)` 格式 |
| QQNT：QQ Chat Exporter 等导出工具 | `.json` / `.jsonl`，字段名宽松匹配（`sender.uin`、`content.text`、`timestamp` 毫秒…） |
| NapCat / OneBot 消息日志 | `.json` / `.jsonl`，支持消息段数组和 CQ 码 |
| 自己整理的 | `.jsonl` / `.csv`，字段 `time, sender_id, sender_name, text` |

识别不了的格式，在 `piao/chatlog/parsers.py` 的 `record_to_message` 里加几个字段名即可。

只导出“飘的发言”也能用，但**整个群的完整记录效果好得多**：既能学飘怎么接别人的话，也能对比出哪些词是飘独有的口癖。

## 第二步：生成 Skill

```bash
cp persona/persona.example.md persona/persona.md   # 填写人设：打法、性格、群友关系、名场面……
piao build data/raw/chat/
```

输出：

- `data/work/profile.md`：风格统计（消息长度、连发习惯、标点、口癖、常用短句、活跃时段、最常接谁的话）。先看一眼，统计不对说明导出格式有问题。
- `skill/qingqingdepiao/SKILL.md`：Skill 本体。人设部分来自 `persona.md`，**写什么分身就信什么，不确定的别写**。
- `skill/qingqingdepiao/references/examples.md`：自动挑出的 40 段真实对话（聊牌的、短回复、长回复都有）。

改完 `persona.md` 后只需重新运行 `piao skill`。想在 Claude Code 里直接用这个 Skill：

```bash
cp -r skill/qingqingdepiao ~/.claude/skills/
```

## 第三步：本地试聊

```bash
piao chat --name 张三
> 今天天凤又四位了
  轻轻的飘：绷不住了
> 李四：飘 23466m345p11s5678p 切啥
  轻轻的飘：切5p
```

`名字：内容` 可以切换说话人，`~` 开头只加入记录不触发回复（用来模拟群里的上下文），`piao -v chat` 能看到发给模型的完整提示词。

觉得不像本人时，按这个顺序调：补充 `persona.md` → 看 `profile.md` 的统计对不对 → 加大 `piao skill --examples`。

## 第四步：上 QQ

1. 用一个**小号**登录 [NapCat](https://github.com/NapNeko/NapCatQQ)，在它的 WebUI 里新建一个 **WebSocket 服务器**（正向 WS）：地址 `127.0.0.1`，端口 `3001`，设置 token，消息格式选 `array`。
2. 修改 `config.toml`：

   ```toml
   [bot]
   ws_url = "ws://127.0.0.1:3001"
   access_token = "你在 NapCat 里设置的 token"
   groups = [829124409]     # 主群；只在这些群里说话，要加别的群往里追加
   admins = [你的QQ号]
   ```
3. `piao bot`

什么时候说话：

- 被 @ 或被回复：必回（引用对方的消息）
- 消息里出现 `aliases` 里的称呼（飘、飘飘……）：85% 概率回
- 平时：按 `random_reply_prob` 偶尔插话，有冷却；模型觉得本人不会接话时会已读不回
- 触发后会等 `debounce_seconds` 秒，把对方连发的几条收齐再回；回复按打字速度分条发出
- 每分钟最多 `max_replies_per_minute` 条，防止和别的机器人互刷

群管理命令（`admins` 里的人可用）：`/飘 off` 闭嘴，`/飘 on` 恢复，`/飘 status` 状态。

**何切**：群消息里出现 13 或 14 张的手牌记号（`123m456p789s1122z`，`0` 是赤五）时，程序会先算出各切法的向听数和进张，作为参考数据交给模型，模型再按飘的风格给结论。也可以自己用：`piao hand 23466m345p11s5678p`。

**模型**：默认 `claude-opus-5-5`，`effort = "low"`（群聊要快）。觉得回复不够有梗可以调到 `medium`。人设部分走 prompt cache，连续对话时大部分输入按缓存价计费。另外默认开启了服务端 fallback：模型偶尔因安全分类器拒答时会自动换模型重试，不想要可以设 `fallbacks = false`。

## 语音

### 用 GPT-SoVITS，而不是 so-vits-svc

so-vits-svc 是**变声**（输入一段人声，换成飘的音色），机器人只有文字，要用它还得先 TTS 再变声。
[GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS) 直接**从文字合成**，几分钟到一小时的素材就能训练，所以这里用它。

### 1. QQ 语音：最干净的素材

QQNT 的语音文件大致在 `Tencent Files\<你的QQ号>\nt_qq\nt_data\Ptt\` 下，扩展名多是 `.amr`，内容其实是腾讯版 SILK。文件本身不带发送者信息，需要先挑出飘发的那些（导出工具如果带资源文件和发送者，可以按记录对应；否则只能人工听）。放进 `data/raw/voice_qq/` 后：

```bash
piao voice decode data/raw/voice_qq data/work/voice/qq_wav
```

这批 wav 一定是本人，后面既能直接当训练素材，也是过滤直播素材的“声纹参考”。

### 2. 直播录像：量大但脏

```bash
# 抽音轨，按 10 分钟切块
piao voice extract data/raw/live data/work/voice/live_wav

# MSST 分离人声 → 去噪/去混响（模型和步骤在 config.toml 的 [voice] 里配置）
piao voice msst data/work/voice/live_wav data/work/voice/msst

# 切成 3~10 秒的片段
piao voice slice data/work/voice/msst/stage1_vocals data/work/voice/live_clips

# 用 QQ 语音做参考，去掉队友连麦、观众、游戏角色语音（雀魂/天凤的“立直！”“荣和！”）
pip install resemblyzer
piao voice filter data/work/voice/live_clips data/work/voice/qq_wav data/work/voice/clean --threshold 0.75
```

- MSST 指 [Music-Source-Separation-Training](https://github.com/ZFTurbo/Music-Source-Separation-Training)，模型从它的 README 下载。第一级用人声分离模型（`keep = "vocals"`），第二级按需要接降噪或去混响模型，`keep` 写该模型 config 里 `instruments` 中要保留的那一项。习惯用 MSST-WebUI 的话也可以在 WebUI 里处理完，直接从 `slice` 这一步接上。
- `filter` 会打印相似度分布：阈值太高会丢掉情绪激动时的语音，太低会混进别人的声音。挑几条边界附近的听一下再定。

### 3. 转写并训练

```bash
pip install funasr modelscope   # 或 faster-whisper（--engine whisper）
piao voice asr data/work/voice/clean data/work/voice/piao.list
```

生成的 `.list` 是 GPT-SoVITS 的标注格式（`路径|说话人|ZH|文本`）。在 GPT-SoVITS 的 WebUI 里校对一遍标注，然后做数据格式化、微调 SoVITS 和 GPT 两个模型，具体步骤以它的文档为准。

### 4. 接入机器人

启动 GPT-SoVITS 的 API（`python api_v2.py -a 127.0.0.1 -p 9880`，并加载训练好的权重），然后在 `config.toml`：

```toml
[voice]
enabled = true
ref_audio = "/GPT-SoVITS那台机器上的/参考音频.wav"   # 3~10 秒、语气自然的一句
ref_text = "参考音频里说的话"
```

`piao voice say "这把切8s就完事了"` 先试听。开启后，别人让飘“发语音”时会发语音，平时短回复也有 `probability` 的概率变成语音。语音在本地转成 QQ 的 SILK 格式再发送。

## 进阶：微调本地模型

数据够多（几千条以上）时，用 LoRA 微调一个本地模型，口吻会比提示词更像：

```bash
piao export-sft --format sharegpt   # LLaMA-Factory 可直接用；--format openai 是 messages 格式
```

## 目录

```
piao/
  chatlog/   解析（parsers）、清洗与脱敏（clean）、构造样本（dataset）
  persona/   风格统计（profile）、Skill 生成（skill）、相似场景检索（retriever）、提示词（prompt）
  mahjong/   手牌解析、向听数、进张、何切分析
  bot/       OneBot 客户端、发言策略、回复生成、运行时
  voice/     SILK 编解码、直播素材处理、GPT-SoVITS 客户端
  templates/SKILL.md   Skill 模板
persona/persona.example.md   人设模板
config.example.toml          配置模板
```

开发：`pip install -e ".[dev,voice]" && pytest`
