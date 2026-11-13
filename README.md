# Vocab Media Finder · 词汇偶遇：视频＋音乐

[English](README.en.md) · [MIT](LICENSE) · Python 3.10+ · 不新增字幕或音乐服务 Key

把一份词表或文章交给 Agent，找到能理解的视频，并在音乐偏好可用时补充英文歌曲。一个 skill、一次导入、一个运行入口，保留实际词形、频次和核验依据。

> 找我能看懂的视频，也按我网易云或 QQ 音乐偏好找英文歌；给我直达链接、命中词族、实际词形、次数和核验状态。

## 直接试用统一版

- [WorkBuddy 单包](https://github.com/jterroblate/vocab-video-finder/releases/download/unified-v4.0.0-beta.1/vocab-media-finder-4.0.0-beta.1-workbuddy.zip)：只导入这个 ZIP。
- [Codex 单包](https://github.com/jterroblate/vocab-video-finder/releases/download/unified-v4.0.0-beta.1/vocab-media-finder-4.0.0-beta.1-codex.zip) · [DeepSeek Harness 单包](https://github.com/jterroblate/vocab-video-finder/releases/download/unified-v4.0.0-beta.1/vocab-media-finder-4.0.0-beta.1-deepseek.zip)
- [全部下载与版本说明](https://github.com/jterroblate/vocab-video-finder/releases/tag/unified-v4.0.0-beta.1) · [统一版使用指南](unified/README.md)

每个统一 ZIP 只有一个根 `SKILL.md`，内部带齐视频与音乐模块，无需安装旧的两个 skill。安装请使用对应 ZIP；源码运行须保留整个仓库，不能只复制 `unified/` 子目录。根目录旧 `SKILL.md` 保留给视频 v3 兼容使用。

## 一条命令看演示

源码仓库根目录：

```sh
python3 unified/run.py demo --output demo-output
```

安装包内则运行 `python3 run.py demo --output demo-output`。同一份合成词表真实通过视频与歌曲匹配器，生成一份 [演示报告](unified/examples/demo-preview.md)；不联网、不读取账号、不含真实观看链接，适合先体验和录屏。

```mermaid
flowchart LR
  A[词表或文章] --> B[扩展并复核词族]
  B --> C[视频与字幕核验]
  B --> D[音乐偏好与歌词核验]
  C --> E[链接、词形、频次、证据状态]
  D --> E
```

正式查找由有工具能力的宿主执行真实搜索和复核；`plan` 只是计划。网易云 macOS 本地读取已实测，QQ 公开接口已联调、本地读取仍实验；Windows 本地音乐读取暂不支持。WorkBuddy/DeepSeek 原生运行仍待验证，当前有单包及跨目录行为测试。个人歌单、学生资料、数据库、凭据和完整歌词不会进入发行包。

## 旧版独立组件

如果项目帮你省下了找材料的时间，欢迎 Star 收藏，也欢迎贡献简单视频来源、平台适配案例和词族误匹配修正。

以下保留视频 v3 与音乐 v0.3 的原有说明和下载。新试用推荐上面的统一版。

<details>
<summary>展开旧视频版说明与独立组件入口</summary>

### vocab-video-finder v3

[English](README.en.md) · Python 3.10+ · MIT · 无额外字幕 API Key

把近期词表放回有趣、可理解的英语视频里。给有工具能力的 Agent 一份词表或文章，它会按场景寻找故事、生活短剧、对话和动画，再用完整字幕核实实际词形、覆盖和频次。

**先选看得懂的内容，再看能复现多少词。** 默认入门档，不把 TED 演讲当所有人的起点。

## 一分钟离线体验

下载仓库，在仓库根目录运行：

命令里的`python`指Python3解释器；环境只有`python3`时请用它替换。

```sh
python scripts/demo.py --output demo-output
```

只需 Python 和标准库，不联网、不安装词库。合成故事会展示4个词族、14次出现及每个实际词形；结果不是公开视频推荐，没有虚构观看链接。

## 按学习者选择

| 档位 | 选材方向 | 默认时长 |
|---|---|---|
| `starter` 启蒙 | 动作、短句、重复故事 | 20秒—3分钟 |
| `easy` 入门（默认） | 清晰日常对话、分级短剧 | 1—6分钟 |
| `standard` 日常进阶 | 自然短访谈、生活情境 | 2—10分钟 |
| `advanced` 挑战 | 密集、抽象或专业内容 | 3—15分钟 |

这是教学检索档位，不是 CEFR 认证。官方等级与本工具估级分别记录；短、儿童或慢速均不自动等于简单。来源库包含官方A1/A2的 [Starting Out](https://learnenglish.britishcouncil.org/free-resources/general/video-series/starting-out)、初级生活故事等，[查看来源卡](references/sources.json)。

| 观看网络 | 最终交付入口 |
|---|---|
| `domestic` 国内（默认） | B站、抖音、小红书 |
| `global` 可访问海外 | 上述平台 + YouTube及其他公开视频页 |

不检测或修改网络。代理能读取海外原稿时，可核验国内同版视频；这不代表学习者能打开海外链接。抖音、小红书可能有登录或工具限制，结果如实标记；短链须解析，图文不当视频。

## 不是逐字搜原词

`compete → competes / competed / competing / competition / competitive` 都可参与词族检索；`written`、`competing` 等输入也会先恢复词典原形。三单、分词、不规则变化和比较级均有回归验证。

场景和同义词扩大召回；最终只计正文实际出现的原词、屈折和已确认派生。`verbal → words` 这种纯语义关系不进入派生计数。每个结果保留语境复核，避免高频的一个词压过整组覆盖。

完整自动词典扩展可选安装 LemmInflect 与 NLTK WordNet：

```sh
python scripts/setup_resources.py --install
```

资源无法下载时，代理可提出并复核显式 `forms/related`；不宣称没有词库也完成了全部自动扩词。无 Python 的宿主只能辅助整理，不能声称跑过脚本核验。

## 使用自己的词表

```sh
python scripts/video_pipeline.py expand --words examples/words.json --output families.json
python scripts/video_pipeline.py discover --families families.json \
  --profile examples/domestic-easy.json --scene "friendly competition" --output plan.json
```

`discover` 输出搜索计划。**Agent 需要用宿主的真实搜索工具执行它**，取得完整文稿后再匹配、复核和选组；CLI本身不冒充全网视频搜索器。[完整步骤](references/workflow.md)

自然语言也可以直接说：

> 用这份词表找入门英语故事和日常对话。我只能看国内平台，请给具体视频链接、实际词形、词频和难度依据。

## 跨工具接入

```sh
# WorkBuddy：生成本地上传ZIP，不猜安装目录
python tools/prepare_package.py --host workbuddy --author YourName --output ../workbuddy.zip

# DeepSeek Harness：明确指定项目技能目录
python tools/prepare_package.py --host deepseek --output ../my-project/.dsh/skills/vocab-video-finder
```

[Codex](adapters/codex.md)保留自动调用；[WorkBuddy](adapters/workbuddy.md)、[DeepSeek Harness](adapters/deepseek.md)有独立接入说明及[可复制自适配提示词](adapters/bootstrap.md)。已测试包结构和文档契约，尚未在本机运行WorkBuddy/DSH原生环境。模型账户/Key属于宿主既有配置；本项目不增加字幕服务Key要求。

## 可选音乐组件

[music-preferences-import](companions/music-preferences-import/README.md) 可在 macOS 上自动检查网易云音乐或 QQ 音乐的可用偏好，按同一套词族规则筛选英文歌曲，不先要求用户找链接、ID 或额外 API Key。它与视频 skill 独立、可组合使用；网易云本地链路已实测，QQ 本地读取仍为实验适配。

在 [music-v0.3.0 发布页](https://github.com/jterroblate/vocab-video-finder/releases/tag/music-v0.3.0)选择独立音乐包或视频＋音乐组合包。音乐包自带匹配核心，附离线合成演示，适合在另一台电脑试用和录屏；发送组件保持独立。

## 贡献与边界

结果同时说明文稿、转载对应、访问和播放检查状态。没有证据不凑数，不下载媒体，不自动发消息。开源包仅含通用代码与合成示例，不含学生资料、完整第三方稿件、缓存或凭据。

欢迎提交误匹配、简单视频来源和平台访问案例，见[贡献指南](CONTRIBUTING.md)。如果它节省了你找材料的时间，欢迎点一个 Star，方便找回，也帮助其他人发现它。

</details>
