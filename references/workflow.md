# 视频检索与核验工作流

## 调用与资源

`VVF` 指向实际 `SKILL.md` 所在目录，不依赖宿主名称或固定home路径。以下初始化示例假定已经进入技能根目录；随后可以从其他工作区用该绝对路径执行，输入和结果留在调用工作区。

```sh
VVF="$PWD"
python3 "$VVF/scripts/setup_resources.py"
```

扩展依赖 LemmInflect 与 NLTK WordNet。上面的命令只检查；需要时运行 `python3 "$VVF/scripts/setup_resources.py" --install`，安装到当前用户独立缓存，不更改项目依赖。当前 Python 次版本没有资源时会明确警告并回退显式词形；不使用后缀猜测替代词典。

默认流程不读取、要求或配置第三方服务 Key。视频发现依靠公共索引和 当前Agent宿主可用的公开网页搜索，核验依靠原版公开逐字稿、对应字幕页或已合法取得的字幕文件。不要让用户申请字幕 API Key，也不将缺 Key 当作任务未完成的原因。

这里的搜索由当前Agent宿主提供，不限于Codex。可选词库若不可下载，由代理提出显式合法词形后逐条复核，不能宣称完成全部自动词典扩展。`VOCAB_VIDEO_CACHE_DIR` 可统一指定缓存和词库目录；不需要复制任何用户私密配置。

## 难度与观看配置

默认 `domestic + easy`。可复制 `examples/domestic-easy.json` 为调用工作区配置，以 `--profile` 指定；显式CLI参数优先。`--network global` 才扩大观看入口到YouTube和其他平台。`--platforms` 可进一步选择子集，国内档要求YouTube会明确报矛盾配置。网络配置约束观看者入口，不禁止有能力的代理读取对应海外原稿。

`--level starter|easy|standard|advanced|any`（别名`--difficulty`）选择教学档位，默认时长分别20—180/60—360/120—600/180—900秒；`any`不筛难度。时长可显式覆写，不能用时长替代难度判断。优先按词义组织 `groups.json` 的场景名称，或给 `--scene`；这由Agent完成，不要求用户手写所有检索式。

```sh
python "$VVF/scripts/video_pipeline.py" discover --families families.json \
  --network domestic --level easy --scene "cooking and sharing a meal" --output candidates.json
```

结果先按场景/档位与平台召回，再检查实际词形，避免只做原词精确共现。查询中的场景同义词不能计入目标词频。

## 输入与词族

`words.json` 接受旧版字符串／`word + forms` 数组，并可补充词性、义项及确认的派生关系：

```json
[
  {"word":"compete", "pos":"v", "forms":["competes","competed","competing"],
   "related":[{"word":"competition","forms":["competitions"],"confirmed":true},
              {"word":"competitive","confirmed":true}]},
  {"word":"breed", "pos":"v", "forms":["bred"]},
  {"word":"dim", "pos":"adj", "forms":["dimmer","dimmest"]}
]
```

`forms` 仅放屈折变化；派生词放 `related`，不把同义词混入。人工关系默认已确认，也可显式设 `confirmed:false`。自动词典候选一律待复核，不覆盖人工输入。同族多个源词条经确认后，在 `related` 中记录连接再重新扩展，使其合并为一个词族；原始词条和各自对应关系保留。

```sh
python3 "$VVF/scripts/video_pipeline.py" expand --words words.json --output families.json
python3 "$VVF/scripts/video_pipeline.py" discover --families families.json --groups groups.json --network domestic --level easy --output candidates.json
```

`groups.json` 示例：`[["compete","competition"],["breed","dim"]]`，实际按场景和未覆盖词优先级编组。网页检索每次用少量有区分度的原词或已知变式，配合场景词、`transcript`、`subtitles` 等；自动生成的查询是起点，Agent应根据结果调整题材和组合。未给分组只按输入顺序编组，不代表自动理解了题材，也不表示整张词表均已检索。词族内 OR 的长表达式不作为普通网页默认查询。

## 候选入口

```sh
python3 "$VVF/scripts/video_pipeline.py" discover --families families.json --groups groups.json --network domestic --level easy --output candidates.json
```

先查 `~/.cache/vocab-video-finder/catalog.sqlite3`，同时生成有界的普通网页搜索计划。`--catalog-only` 可仅查本地；`--cache-dir` 用于隔离验收缓存。`discover` 不发起需 Key 的 API 请求，也不自动搜索网络；应由Agent使用当前可用网页搜索工具执行 `web_search_queries`，并根据 `search_plan` 继续。

本地检索分、搜索标题和摘要都不是确认词频。即使本地已有候选，也应按本次覆盖需要补充新题材，避免总从同一历史清单挑选。没有本地命中时直接继续公开网页检索，不停下来索要密钥。

检索分两步：先以“场景或少量词形 + transcript / subtitles”寻找原版视频及全文；再用已找到的英文标题、作者或中文译名搜索 `site:bilibili.com/video`。B站同名视频可能是别的内容，须核对身份和实际分集。场景同义词可帮助找题材，最终只计正文中的目标词和已确认词族成员。

原版文稿优先找官方 `Transcript` / `Script`；例如故事站的故事原文、实景对话的脚本和访谈字幕。不设固定来源配额。标有“非逐字稿”、摘要、改写或练习填空的正文不能直接用于精确词频；某来源登录或受阻时换公开来源或候选，不把该服务作为必经入口。

可用于拓展题材的公开入口包括 [British Council 实景对话](https://learnenglish.britishcouncil.org/free-resources/general/video-series/wots/art/art-scene-2) 的 `Transcript`、[ELLLO 生活访谈](https://elllo.org/video/M126/T150-BadHabit.htm) 的 `Script`，以及与原视频 ID 对应的公开字幕页。故事或访谈较短时按本次时长要求取舍，不把栏目简介和练习正文混入。

公开页的可读视图缺少字幕时，可检查已公开返回的原始 HTML 是否包含字幕数据。例如 [VoiceTube 对应 BBC 节目页](https://www.voicetube.com/videos/197901/86339344) 的公开响应包含 `captionLines`，应只提取 `originalText.text` 和真实 `startAt/duration`，核对同一原视频 ID 并检查首尾完整性；词卡和整页文本不可计数。只解析公开数据，不执行网页中的代码，也不将此单站作为必需依赖。

## 正文与时间

```sh
# 官方 TED 页含与视频 URL 对应的完整 JSON-LD transcript
python3 "$VVF/scripts/video_pipeline.py" transcript --url 'https://www.ted.com/talks/实际视频标识' --output document.json
# 已合法取得的本地字幕／正文
python3 "$VVF/scripts/video_pipeline.py" transcript --input captions.vtt --format vtt --source-url '原字幕来源URL' --output document.json
```

正文来源优先级为官方逐字稿、与原版明确对应的公开字幕页、可取得的字幕文件。`--url` 内置获取目前处理 TED 官方页；其他页面由可用网页工具读取并提取真正的完整 `Transcript` / `Script`，保存为工作区纯文本后执行 `transcript --input transcript.txt --format text --source-url '实际全文URL'`。不要把 HTML 页面、导航、练习或评论整页输入。

格式支持 `text`、`srt`、`vtt`、`ted_jsonld`、`bilibili`，另保留旧字幕 JSON 本地导入兼容。统一清洗标签及有真实时间重叠的滚动重复，末句须保留。源记录的 `completeness` 是解析诊断，仍需检查明显截断、语言与误字。

规范文稿含 `text`、`cues`、来源、语言、自动字幕标记、SHA256 与解析版本。cue 的 `char_start/char_end` 用于映射真实字幕时间；无真实时间时保留 null，只有开始时间时不能补造结束时间。

## B 站分集与证据

```sh
python3 "$VVF/scripts/video_pipeline.py" bilibili --bvid '已观察到的BV号' --cid 真实cid --refresh --inspect-subtitles --output bili.json
```

优先按 `bvid + cid` 解析真实 `page`；已确认具体分集时也可 `--page`。合集有多集而未指定时返回待选状态，不默认交付第一集。交付前加 `--refresh` 重查，链接使用解析结果的实际页码，不依赖标题中的 “p36”。`--fetch-subtitles` 可尝试取得英语字幕正文；登录限制返回 `login_required`，不等于无字幕。

分别记录原版文稿、原版与转载对应、B 站字幕正文取得、实际播放四类证据。API 页面成功只证明元数据，不证明原声、字幕或播放。中文配音不能作为英语原声材料。原稿与转载有删改或身份不清时，不将原稿次数当作该转载的已核实次数。

## 匹配与人工复核

`video.json` 是本次观看入口的元数据：`id`、`title`、`url`、`duration_seconds`；可补 `canonical_id`（原版身份）、`bvid/cid/page`、`genre`、`topic`。证据标记默认未确认，由实际核验更新。

```sh
python3 "$VVF/scripts/video_pipeline.py" match --families families.json --document document.json --video video.json --output match.json
python3 "$VVF/scripts/video_pipeline.py" review --report match.json --output decisions.json
```

`match` 接受规范文稿或包含单篇规范文稿的来源响应。检查每个 occurrence 的上下文、实际词形、来源、派生关系及义项后，编辑 `decisions.json`：

- `action`：`accept` / `exclude` / `uncertain` / `pending`；不确定项听辨解决或排除，不能混入确认数量。
- `relation_confirmed:true`：接受自动生成的词形／派生关系时须明确确认；不能批量把所有词典候选当正确命中。
- `sense`：`same` / `new` / `unspecified`；同词不同义仍计入主数，标 `new` 并写简短 `note`，不称为原义强化。
- `video_review`：英语原声、自然语境、原版身份对应和字幕证据分别用 `english_audio_confirmed`、`natural_context`、`source_identity_confirmed`、`subtitle_available` 确认。`full_transcript_confirmed:true` 须确认是全片正文；片段不可作为全片统计入选，解析器的 `completeness` 诊断不自动证明完整。`subtitle_available` 指有可供学习者使用的字幕证据；仅取得原稿不能据此断言转载字幕可用。B 站另确认 `part_mapping_checked`。`subtitle_body_checked` 与 `playback_checked` 只有实际取得／播放后才置 true；其余保留 false。证据标记必须为 JSON 布尔值。

`video_review.difficulty_review` 必须有 `level`、`confidence:medium|high` 与非空 `basis`，具体说明词汇、句式、语速/清晰度、画面与字幕支持；`provider_level` 只抄官方原标签并留来源，不自动换算。依据缺失或超档会在精选前拒绝。允许更容易档位，不能为命中数默默提高难度。

`access_status` 为 `unknown/public/login_required/blocked`，表示观看入口而非字幕接口。未知状态照实输出，不称播放已检查。抖音/小红书短链需 `link_resolved:true` 加同平台最终 `resolved_url`；小红书和其他站必须 `video_format_confirmed:true`。小红书图文、搜索页和频道页不入选；观察到观看登录限制或受阻时排除。平台内搜索/网页搜索是否执行另留在本次检索记录。

同一位置匹配两个未合并词族时，须确认词族关系后重新扩展／匹配，或排除不适用候选；不能同时接受来虚增词族数。重复接受同一物理位置不会增加总数。同族多个源词条的 `source_target_hits` 可分别查阅，其数量可能重叠，不能相加当作总出现次数。

```sh
python3 "$VVF/scripts/video_pipeline.py" review --report match.json --decisions decisions.json --output reviewed.json
```

`reviewed.json` 保留最小命中上下文、字符与真实时间、来源及哈希、逐条决定、词形统计和元数据。它不保存整篇稿件，足够日后重新选组／导出；词表改变或正文改变时必须重新匹配，不复用旧决定。候选尚有 pending/uncertain 时不能进入精选。

同一整组必须使用同一份 `families.json`，摘要中的词汇范围哈希不同会拒绝合并。补充确认关系导致词族调整后，重跑本组所有候选，而非混合新旧摘要。

## 整组排序与导出

```sh
python3 "$VVF/scripts/video_pipeline.py" select --reports reviewed-1.json reviewed-2.json --network domestic --level easy --limit 8 --output bundle.json
python3 "$VVF/scripts/video_pipeline.py" render --bundle bundle.json --output videos.txt
```

`--platform any` 表示不再缩小profile平台列表，并不代表默认允许海外入口；旧单平台参数仍支持。先筛网络、难度、证据、语言、视频形式与所选时长，再按新增覆盖（权重3）、`log(1+频次)` 的整组增益与少量内容类型新颖度选择。按原版身份和文稿哈希去重，真实次数完整显示。旧v2摘要可直接render；重新select时须补难度，或明确用`--level any`关闭难度过滤，不能伪造等级。

导出包含词族覆盖、直接命中与仅派生关联词族数、每族及实际词形频次、派生关联与新义项标记、时长和具体 HTTPS 直达链接。按原稿统计且转载字幕／播放未检查时注明范围。输出是可转发纯文本；`render` 不接触发送记录，默认拒绝覆盖已有 TXT，明确需要时可 `--overwrite`。没有文稿时也可从已复核摘要重导出。

## 公共索引与留档

确认视频与稿件均为公开内容后，可入公共索引：

```sh
python3 "$VVF/scripts/video_pipeline.py" catalog-add --video video.json --document document.json --public
```

索引长期保留白名单元数据和无序词频，完整字幕只在短期缓存（24 小时），启动时清理过期稿件。公开网页搜索词、学生词表、命中上下文复核与导出清单不写入公共索引；清洗后的公开视频文稿可在短期内跨工作区复用。

本流程只生成内容，不管理联系人、渠道或提交记录。消息分发属于独立组件，不是本 skill 的必需依赖。

## 依据与验收

词族资源变化时核对一手文档：[LemmInflect](https://github.com/bjascob/LemmInflect/blob/master/docs/inflections.md)、[WordNet 派生关系](https://www.nltk.org/howto/wordnet.html)。

```sh
python3 -B -m unittest discover -s "$VVF/scripts" -p 'test_*.py'
```

另在技能目录外运行完整 CLI 冒烟，确认无凭据时能正常生成搜索计划，并实际完成公开网页检索、全文核验和观看入口定位。重要升级用独立代理拿真实请求和原始测试材料前向执行；历史批次比较须统一统计口径，义项规则变化产生的数量变化不能冒充检索提升。历史已发送结果保持原样。
