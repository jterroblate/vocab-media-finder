# Codex

将包放入实际Codex skills目录下的 `vocab-video-finder/`。源码的 `agents/openai.yaml` 保留 `allow_implicit_invocation: true`，同时可显式使用 `$vocab-video-finder`。

从源码使用 `tools/prepare_package.py --host codex --output <明确选择的新目录>`；工具不猜宿主目录，也不覆盖已有非空目录。已有安装先备份，确认无用户数据后更新。脚本以实际SKILL目录定位，不依赖当前工作目录。

搜索、正文和播放工具是否可用取决于当前会话；首次读[bootstrap](bootstrap.md)。发送能力始终独立，不随本包安装。
