# Vocab Media Finder · 词汇偶遇：视频＋音乐

[English](README.en.md) · `4.0.0-beta.1` · Python 3.10+ · MIT

把一份词表或一篇文章交给有工具能力的 Agent，寻找能看懂的视频，并在可用时按音乐偏好补充英文歌曲。让单词回到故事、生活和音乐中；输出实际词形、频次和核验依据。

> 找我能看懂的视频，也按我网易云或 QQ 音乐偏好找英文歌；给每条材料的直达入口、命中词族、实际词形、次数和核验状态。

[下载统一版](https://github.com/jterroblate/vocab-video-finder/releases/tag/unified-v4.0.0-beta.1) ｜ [GitHub 仓库](https://github.com/jterroblate/vocab-video-finder)

[先预览合成演示报告](examples/demo-preview.md)，或让 Agent：“运行统一版离线演示，直接展示汇总报告，先不要访问我的账号。”

## 一次导入，一个入口

统一版只有一个 `SKILL.md`、一个安装包和一个 `run.py`，视频与音乐是内部模块，无需分别安装。下载对应宿主的 ZIP；WorkBuddy 直接导入一个 ZIP，Codex 和 DeepSeek Harness 安装一个技能目录。首次运行可把[自适配提示词](adapters/bootstrap.md)交给宿主，让它检查实际文件、Python和工具能力。

原生 WorkBuddy/DeepSeek Harness 尚未逐台实测；当前验证了包结构、跨目录运行和降级契约。模型账户使用宿主本来的配置，本项目不新增字幕或音乐服务 Key。

## 先用安全的离线演示录屏

在解压后的技能目录运行：

```sh
python3 run.py demo --output demo-output
```

源码检出时，保留完整仓库，在仓库根目录运行 `python3 unified/run.py demo --output demo-output`；不要只复制 `unified/` 子目录。同一份合成词表会进入视频合成正文和两个音乐模式的自写歌词，一次生成 `demo-report.md` 与 JSON。不读取账号、不联网、不含真实观看链接，适合先熟悉流程和录屏。

## 按难度与网络找内容

视频支持 `starter` 启蒙、默认 `easy` 入门、`standard` 日常进阶、`advanced` 挑战四档。它们是检索和人工复核档位，不是 CEFR 认证。`domestic` 默认交付 B站、抖音、小红书入口；`global` 再加入 YouTube 等平台。短或慢速不自动等于简单，先判断能否理解，再比词族覆盖与重复曝光。

原词、三单、时态、复数、不规则分词、比较级和确认派生都可参与匹配。LemmInflect/WordNet 是可选本地资源；没有词库时，Agent可提出并复核 `forms/related`。普通同义词帮助找题材，不计入目标词命中。

## 音乐偏好

Agent 先检查本机支持的客户端，不要求新手找分享链接、数字 ID 或 Cookie。网易云音乐 macOS 3.1.12 已实测红心和近30天本机去重记录；QQ公开元数据、歌手作品和歌词已联调，本地缓存明确关联 schema 仍为实验适配，未在本机原生客户端实测。Windows 本地读取暂不支持。

多账号由 Agent 核对当前客户端；缓存不是认证证明，近期行数也不是听歌次数。同歌手的新作品不等于已喜欢。用户只要视频时，不读取音乐账号；音乐不可用时，视频工作继续。

## 正式查找与证据

由 Agent 提取词表、扩词族和复核，再准备统一任务：

```sh
python3 run.py plan --words words.json --output plan-output --mode both --network domestic --level easy
```

`plan` 是待宿主执行的检索和核验计划，CLI 本身不会完成全网搜索。宿主用真实工具取得字幕/歌词正文，核实语境、版本、访问、难度和播放范围，再筛选互补材料。标题不计词频，同一出现只计一次；输出词族覆盖、原词/屈折/派生、总次数、每词形式及证据状态。未完成音乐复核时保持 `ready_to_deliver=false`。

可用 `run.py video ...` 调用内部视频流程，`run.py music ...` 调用内部网易云/QQ流程；这些内部步骤由 Agent 操作，用户无需管理多个技能。

## 公开项目与个人数据

公开 ZIP 只含通用代码、文档和合成示例；不包含个人歌单、学生词表、完整歌词、客户端数据库、凭据或真实输出。实际资料留在调用工作区。项目不内置联系人、发送或日程自动化。旧视频 v3、音乐 v0.3 的发行包保留兼容；新试用推荐统一版。

如果它帮你省下了找材料的时间，欢迎 Star 收藏；也欢迎贡献简单内容来源、客户端适配和误匹配修正。
