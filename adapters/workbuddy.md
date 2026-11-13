# WorkBuddy

从源码仓库运行 `python tools/prepare_package.py --host workbuddy --author YourName --output ../workbuddy.zip`，然后按[官方本地技能包说明](https://www.workbuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/Skills-Market)导入ZIP。已导入的包不需要再次运行打包工具。

转换器根据[官方规范](https://open.workbuddy.cn/docs/skill)补齐市场要求的展示名、中英文简介、版本和作者等frontmatter；正文与脚本共用核心。不要猜通用 `~/.workbuddy/skills` 目录，也不假定 `WORKBUDDY.md` 或Codex元数据自动加载。需要时显式引用本包 `SKILL.md` 与[bootstrap提示词](bootstrap.md)。

代码开发场景的CodeBuddy目录支持与WorkBuddy通用办公的包导入是不同接入路径；以当前宿主实际发现结果为准。已验证转换与打包契约，未在本机WorkBuddy原生环境运行。加载后先检测Python、搜索及抓稿能力。

核实日期：2026-11-13。宿主规范可能变化，遇到解析失败先对照官方文档，不自动改全局权限或安装其他服务。
