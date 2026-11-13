# 其他工具型Agent

读取SKILL.md和[bootstrap](bootstrap.md)，映射宿主真实工具名到文件、Python、搜索、正文与播放步骤。无需Codex SDK；无需把本技能改成某个专用聊天API客户端。

不能将“模型能生成tool call”当作“宿主会执行工具”。若宿主只聊天，提供所需工具说明及能完成的人工辅助，不伪造联网和运行结果。词典资源和工作区均可通过命令参数或 `VOCAB_VIDEO_CACHE_DIR` 配置。

构建通用安装目录或ZIP：`python tools/prepare_package.py --host generic --output <明确目录>`，或加 `--format zip`。命令在源码仓库执行；运行时只需要已安装包里的脚本和引用资源。
