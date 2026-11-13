# 贡献方式

欢迎贡献更简单的公开视频来源、平台访问案例、误匹配回归和宿主适配。请提供可复现步骤与公开URL，不附学生词表、联系方式、凭据或完整受版权保护稿件。

新来源卡请注明官方等级原标记、内容类型、正文位置、所对应的视频/片段和核实日期，不将儿童/慢速等同CEFR。平台验证先区分搜索、字幕和观看访问；小红书图文不当视频，短链需确认最终身份。

词形修改需用合成短句覆盖：实际屈折、派生、同族去重、异义和不应命中的反例。同义词不进入计数；词典提议仍需语境复核。不要用结果文字的快照代替行为验证。

统一入口在 `unified/`，视频引擎在 `scripts/`，音乐引擎在 `companions/music-preferences-import/`；源码运行保留完整仓库，发行安装使用 `prepare_unified_package.py` 生成的单技能包。

```sh
python -B -m unittest discover -s scripts -p 'test_*.py'
python -B -m unittest discover -s tools -p 'test_*.py'
VOCAB_VIDEO_CORE="$PWD/scripts" python -B -m unittest discover -s companions/music-preferences-import -p 'test_*.py'
python -B -m unittest discover -s unified -p 'test_*.py'
python unified/run.py demo --output unified-demo-output
```

原生WorkBuddy/DSH验证请记录宿主版本、操作系统和实际可用工具，明确哪些步骤跑过。包结构测试不能作为原生运行成功证明。个人本地配置与缓存不提交。

新增公开脚本或引用资源时，同时更新打包工具的文件级白名单并补测试，避免把临时笔记、账号脚本或私有材料带入分发包。
