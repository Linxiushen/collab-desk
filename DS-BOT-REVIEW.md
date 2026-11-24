# DS Bot 对协作台（Collab Desk）的参考价值

如果你想让已登录的 Codex、Claude Code 在群里读写项目文件、使用工具并请求审批，**优先试用 DS Bot 桌面版**，它已为 Apple Silicon 提供安装包。如果你现在要断网讨论，**这台 Mac 上的协作台已经具备所需配置**，默认使用本机 Qwen 推理，可以从浏览器直接打开已有讨论室。DS Bot 也能配置模型服务地址、协议和模型 ID。它在这台 Mac 上的离线运行效果仍待实测。[桌面版本][release] · [本地 Agent 说明][readme] · [模型路由配置][providers]

截至 2026 年 11 月 23 日，GitHub 最新正式版为 **v0.3.10**，发布日为 11 月 22 日。本次检查的 main 提交与发布标签一致，完整提交为 `d104d9ca18d69a35460f14e15dff4d553181433a`。下文所述 DS Bot 行为来自固定版本源码、发布清单和上游界面截图。本机运行验证覆盖的是协作台。DS Bot 桌面安装、登录和实际计费仍待试用核验。[发布清单][release] · [固定提交][commit]

| 比较项 | DS Bot 的实际设计 | 对协作台的决定 |
| --- | --- | --- |
| 安装与维护 | 桌面版是 Electron 应用，内含固定版本的 DeepSeek Harness（dsh 0.2.2-rc.1）。另有依赖 dsh 宿主的插件包。Apple Silicon 桌面 ZIP 约 391 MB。 | 想马上用完整工具团队，可独立试用桌面版。若要把它嵌入协作台，需要接入另一套会话、权限和数据存储，先把两者作为独立产品比较。[桌面依赖][app] · [插件依赖][pkg] · [发布包][release] |
| Codex 身份和工具 | 启动 `codex app-server`，使用 CLI 的登录状态。每轮可继续已有线程，传入工作目录与工具权限，命令和文件变更的审批进入 UI。用量事件区分输入、缓存输入和输出。 | 协作台的“本地 Codex”启动真实 CLI，但每个任务建立临时会话，用随应用配置的 Qwen 阅读群聊文字。界面宜直接注明“本机 Qwen · 文字回复”。若扩展到当前登录账户，应单独显示使用的账户或模型、工作目录、审批结果与实际用量。[引擎和登录探测][engine] · [线程和审批][codex] |
| 群内派工与成本 | 新群默认使用管理员模式。普通问题由管理员直接作答，或点名其他成员处理，点名绕过管理员直接叫成员。全员模式的普通提问依次交给成员，管理员优先。群消息路由本身由程序执行。成员调用模型、接力和长会话仍可能消耗额度。 | 输入区展示“发给谁”和将收到多少份回复。发给一个人、等第一份结果后再复核、要求多人各自作答，这三种场景应有不同的操作和反馈。“调用次数”可由任务记录计算，费用则应来自供应商计价或账单。[新群默认值][roster] · [收件人选择][route] · [程序派发][relay] · [用量记录][usage] |
| 跨轮上下文与记忆 | 按“讨论室 × 成员”保存成员会话 ID。另有明确的个人、群和团队记忆范围、Markdown 条目和变更记录。写群记忆前检查成员关系。 | 协作台在此版检测中已能回读 SQLite 聊天记录，追问可带上之前的答复。界面可先让用户找到原文、引用和所属房间。将来加入长期记忆时，应让用户能看见、修改、删除具体条目。[成员会话][sessions] · [记忆文件布局][memory] · [写入权限][scope] |
| 侧栏与团队状态 | 侧栏用会话事件更新最后一句、更新时间和未读结果。成员正在使用的工具和待审批项从当前群的任务状态汇总。官方截图把常用成员和房间放左侧，消息按发言人排列，输入框在底部。 | 左栏支持从预览找到房间，中间保留连续对话，成员设置按需展开。按钮要让人看得出下一条会发给谁。外部成员待领取、正在生成、任务失败和已回帖，应由服务端或任务回执驱动。[侧栏订阅][sidebar] · [成员活动区][activity] · [官方群聊截图][shot] |

**复用边界。** DS Bot 仓库采用 Apache 2.0，NOTICE 另列 DeepSeek Harness 等组件与素材的许可及归属。本轮界面组织借鉴群派发、会话列表和成员状态的组织方式。若后续分发包含上游源码或素材的版本，需按实际使用范围保留许可证与适用的归属声明，并标明修改文件。[LICENSE][license] · [NOTICE][notice]

**运行证据。** 上游提供了 Codex 0.166 app-server 的两轮对话、上下文压缩和再一轮对话的录制事件，可用于核对协议的事件名称。协作台已在本机用 Codex CLI 和 Qwen 完成两轮库存问答。第一轮以 28 减 13 得到 15 箱，第二轮读取前一回答，再减 2 得到 13 箱。[上游事件录制][fixture]

**协作台这次更新。** 左栏可按名称搜索讨论室，结果显示上一条消息的作者和摘要。右侧成员面板列出分工，并根据本房间的任务记录和接单进程显示“等待接单”“正在回复”或“自动接单已开启”。聊天区的回复可复制，带围栏的代码会按代码块排版并保留换行。

[release]: https://github.com/Fei2411/DS-Bot/releases/tag/v0.3.10
[commit]: https://github.com/Fei2411/DS-Bot/commit/d104d9ca18d69a35460f14e15dff4d553181433a
[readme]: https://github.com/Fei2411/DS-Bot/blob/d104d9ca18d69a35460f14e15dff4d553181433a/README.zh.md#L98-L104
[app]: https://github.com/Fei2411/DS-Bot/blob/d104d9ca18d69a35460f14e15dff4d553181433a/app/package.json#L26-L68
[pkg]: https://github.com/Fei2411/DS-Bot/blob/d104d9ca18d69a35460f14e15dff4d553181433a/ds-bot/package.json#L55-L65
[engine]: https://github.com/Fei2411/DS-Bot/blob/d104d9ca18d69a35460f14e15dff4d553181433a/ds-bot/src/host/local-agents/engines.js#L15-L27
[codex]: https://github.com/Fei2411/DS-Bot/blob/d104d9ca18d69a35460f14e15dff4d553181433a/ds-bot/src/host/local-agents/codex.js#L125-L227
[roster]: https://github.com/Fei2411/DS-Bot/blob/d104d9ca18d69a35460f14e15dff4d553181433a/ds-bot/src/host/roster.js#L645-L677
[route]: https://github.com/Fei2411/DS-Bot/blob/d104d9ca18d69a35460f14e15dff4d553181433a/ds-bot/src/host/groups.js#L2067-L2088
[relay]: https://github.com/Fei2411/DS-Bot/blob/d104d9ca18d69a35460f14e15dff4d553181433a/ds-bot/src/host/groups.js#L2493-L2539
[usage]: https://github.com/Fei2411/DS-Bot/blob/d104d9ca18d69a35460f14e15dff4d553181433a/ds-bot/src/host/usage.js#L140-L176
[sessions]: https://github.com/Fei2411/DS-Bot/blob/d104d9ca18d69a35460f14e15dff4d553181433a/ds-bot/src/host/groups.js#L828-L849
[memory]: https://github.com/Fei2411/DS-Bot/blob/d104d9ca18d69a35460f14e15dff4d553181433a/ds-bot/src/host/memory.js#L1-L26
[scope]: https://github.com/Fei2411/DS-Bot/blob/d104d9ca18d69a35460f14e15dff4d553181433a/ds-bot/src/host/memory.js#L487-L507
[sidebar]: https://github.com/Fei2411/DS-Bot/blob/d104d9ca18d69a35460f14e15dff4d553181433a/app/ui/src/team.js#L195-L231
[activity]: https://github.com/Fei2411/DS-Bot/blob/d104d9ca18d69a35460f14e15dff4d553181433a/ds-bot/src/client/member-status.js#L16-L47
[shot]: https://github.com/Fei2411/DS-Bot/blob/d104d9ca18d69a35460f14e15dff4d553181433a/docs/images/group.zh.png
[license]: https://github.com/Fei2411/DS-Bot/blob/d104d9ca18d69a35460f14e15dff4d553181433a/LICENSE#L89-L123
[notice]: https://github.com/Fei2411/DS-Bot/blob/d104d9ca18d69a35460f14e15dff4d553181433a/NOTICE
[fixture]: https://github.com/Fei2411/DS-Bot/blob/d104d9ca18d69a35460f14e15dff4d553181433a/ds-bot/test/fixtures/recorded/codex-app-server-0.166.json
[providers]: https://github.com/Fei2411/DS-Bot/blob/d104d9ca18d69a35460f14e15dff4d553181433a/ds-bot/src/host/providers.js#L319-L332
