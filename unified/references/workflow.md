# 统一词表与内部视频流程

从统一技能根目录执行以下命令，即可在源码和安装包中正确定位内部视频引擎，后续视频工作流里的 VVF 都使用此值。

```sh
VVF="$(python3 -c 'from run import resolve_layout; print(resolve_layout()["video_scripts"].parent)')"
python3 "$VVF/scripts/setup_resources.py"
```

视频正文获取、分集定位和逐词复核细节在 `$VVF/references/workflow.md`；源码原文开头的旧单技能路径约定由上面的统一定位替代，不需要另安装任何 skill。

词典提案先复核，再把确认的 `forms/related` 用于视频和音乐：更新同一份工作区词表后，重新生成视频 families，并将同一词表传给 `run.py music --words ...`；`plan` 仅保留待办，不代表已经搜索、读取账号或完成核验。
