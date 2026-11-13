# DeepSeek Harness

官方Harness支持项目 `.dsh/skills/<name>/SKILL.md` 和 `.agents/skills/<name>/SKILL.md`。从源码仓库可运行：

```sh
python tools/prepare_package.py --host deepseek --output ../my-project/.dsh/skills/vocab-video-finder
```

目标目录必须由用户选择；已有非空目录会拒绝覆盖。`name/description`保留标准frontmatter，不需先编写TypeScript插件。默认允许用户和模型调用，不设置禁用策略。

启动宿主、选择正确工作区后，用[bootstrap](bootstrap.md)确认Skill发现、Python与可选`web_search/web_fetch`等真实工具；没有这些工具时不能承诺完整检索。模型服务账户/Key属于宿主原有配置，本skill不添加字幕服务Key。

已测试包和文档契约，未在本机运行DSH原生环境。官方开发者预览可能有breaking changes；依据[Skills](https://deepseek-harness.github.io/deepseek-harness/en/reference/subsystems/skills)、[Web能力](https://deepseek-harness.github.io/deepseek-harness/en/reference/subsystems/web)与[快速开始](https://deepseek-harness.github.io/deepseek-harness/en/guide/quickstart)，核实日期2026-11-13。
