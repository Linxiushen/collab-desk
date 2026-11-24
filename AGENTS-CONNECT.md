# 让本机已有的 Agent 加入群聊

在网页里点「接入外部 Agent」，填好名称和职责，复制一次性显示的令牌。在讨论室的「管理成员」中勾选这个 Agent 并保存。每个 Agent 使用自己的令牌和本地 profile。例如，让 Codex 负责分析，就为它创建名为 `codex-review` 的 profile。启动工作台后，在本机终端执行下列命令，按提示粘贴令牌。输入不会显示在屏幕上。所需环境为 Python 3.9 或更高版本。

```sh
cd /path/to/collab-desk
python3 agent_cli.py login --profile codex-review
python3 agent_cli.py rooms --profile codex-review
```

登录会先向本机工作台核实 Agent 身份，再将凭据保存为 `~/.pachat-local/profiles/codex-review.json`，文件权限为 `0600`。使用自定义端口启动时，在登录命令后追加 `--server http://127.0.0.1:端口号`，后续命令会使用保存的地址。profile 名称使用字母、数字、连字符或下划线。

## 完成第一次交接

在群聊里给这个 Agent 分配一条消息，再执行 `inbox`。输出中的每个任务都有 `id`、`room_id`、`prompt` 和 `history`。先读群友已经给出的意见，再把回答提交到对应任务。把下例的 `JOB_ID` 和 `ROOM_ID` 换成实际输出的值。

```sh
python3 agent_cli.py inbox --profile codex-review
python3 agent_cli.py reply JOB_ID --profile codex-review --text '我复核了前一条计算，分母应当使用实际付费人数。'
python3 agent_cli.py read ROOM_ID --profile codex-review
```

回到网页，可以看到这条回答的作者以及它对应的提问。如果提交后连接中断，可以用相同任务 ID 和相同正文重试。当 Agent 需要主动在已加入的群里发布补充信息时，可以使用 `python3 agent_cli.py post ROOM_ID --profile codex-review --text '补充材料……'`。这会发送一条独立的群消息。

## 通过 MCP 接入 Codex 或 Claude Code

在本目录执行相应的命令。选择 Codex 时，在上面建好的 profile 下注册本机工具。

```sh
codex mcp add collab-desk -- python3 "$PWD/agent_cli.py" mcp --profile codex-review
```

选择 Claude Code 时，先给它添加一个独立的外部 Agent，运行 `python3 agent_cli.py login --profile claude-review` 并输入它自己的令牌，再执行下面的命令。此处使用 Claude Code 的本地项目配置。请在这个项目中打开 Claude Code 使用。

```sh
claude mcp add --transport stdio --scope local collab-desk -- python3 "$PWD/agent_cli.py" mcp --profile claude-review
```

重新打开对应客户端的会话。在群里给这个 Agent 分配一个问题，再把下面这句话发给它。

> 用 collab-desk 的 inbox 查看分配给你的待办。结合对应的群历史回答这个问题，必要时用 read 读取该群消息，然后用 reply 把结果提交给这个任务。

MCP 提供 `inbox`、`read` 和 `reply` 三个工具。登录和注册只让这些工具可用，收件箱要由正在运行的 Agent 主动读取。需要使用时，在本机打开相应客户端，发出读取收件箱的请求。客户端必须有权访问本机服务、脚本及自己的 profile。如果客户端本身调用云模型，交给模型处理的群消息会进入该服务。

## 排查连接

- 收件箱为空时，可运行 `rooms` 核对当前身份所属的群，再确认网页把任务分配给了它。按顺序协作时，先让排在前面的成员提交回答。
- 出现 HTTP 401 时，核对输入的令牌和工作台地址。若一次性令牌已丢失，可以在网页用新名称创建替代身份，在「管理成员」中换成新成员，再登录并重新分配任务。
- 出现连接失败时，先打开工作台页面确认端口，再核对登录时的服务地址。这套客户端只连接本机回环 HTTP，远端云环境中运行的 Agent 不能直接访问这台 Mac。

## 让本机 Codex 自动回复

如果已经在群里接入了名为 `codex-review` 的外部 Agent，并按上文登录，可以打开终端进入项目目录，运行下面这条命令。使用其他 profile 时请替换名称。

```sh
python3 codex_worker.py --profile codex-review --watch
```

保持终端打开，在对应讨论室中点名这个成员提问。它每 5 秒查看一次自己的待办，并把答案发回原群。按 Control-C 可以停止。只想处理一个当前待办时，去掉 `--watch` 即可。

工作进程调用这台 Mac 的 Codex CLI，由本机 llama.cpp 运行 Qwen3-4B 推理。群里的问题、相关上下文和答复始终留在本机。每个任务使用独立的临时工作目录，读取最近约 4,000 字历史和最多 3,000 字当前问题，仅处理文字。自动回答使用内置的离线职责配置。想使用另一个客户端已有的工具或其他模型，可按上节将群聊 MCP 接入该客户端。

模型启动期间，工作进程会保留待办，待模型就绪后生成答复。生成失败、超时或中断时，群内会显示失败原因和任务编号。若工作进程被强制关闭，下次启动时会补记中断。你可以在群里重新发问，自行管理 profile 的用户也可以停止持续监听后，按编号手动重试。原先的错误记录会保留在聊天历史中。

```sh
python3 codex_worker.py --profile codex-review --retry JOB_ID
```

如果回答已经生成、提交时连接断开，重新启动工作进程即可。它会从本机草稿中读取原文并再次提交。
