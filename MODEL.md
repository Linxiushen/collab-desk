# 本机推理

内置分析员和复核员共享 Qwen3-4B，每个成员根据自己的分工和当前群聊记录发言。复核员能看到前一个成员的结论。两个角色可能犯同样的错误，涉及业务决策的数字仍需核对原始资料。内置成员只能读取传入的群聊文字；它们没有浏览器、文件读取或命令执行工具。

在 Apple Silicon macOS 上可使用 llama.cpp 和权重，聊天内容通过本机回环连接送入模型。第一次安装权重需要联网，此后启动和推理可离线进行。模型权重保存在 `~/.cache/pachat-local/models/`，不占项目目录。

在项目目录运行以下命令可管理模型。

```sh
python3 local_model.py status
python3 local_model.py start
python3 local_model.py stop
```

在另一台 Mac 安装时，先安装 [llama.cpp](https://github.com/ggml-org/llama.cpp)，再在项目目录执行以下命令。已在 llama.cpp build 11429、macOS arm64、24 GiB 统一内存下验证。

```sh
brew install llama.cpp
python3 local_model.py install
python3 local_model.py start
```

安装命令下载官方 Qwen3-4B 的 Q4_K_M GGUF 文件，大小为 2,497,280,256 字节；检查 SHA-256 后才会使用权重。模型许可证为 Apache 2.0。[官方模型及许可证](https://huggingface.co/Qwen/Qwen3-4B-GGUF/tree/bc640142c66e1fdd12af0bd68f40445458f3869b)

```text
SHA-256  7485fe6f11af29433bc51cab58009521f205840f5b4ae3a32fa7f92e8534fdf5
```

默认推理端口是 `127.0.0.1:18765`，模型密钥和运行日志存放在 `~/.cache/pachat-local/`。启动失败时查看 `model.log`。如已在本机运行提供 OpenAI Chat Completions 的模型，启动聊天服务前设置 `PACHAT_MODEL_URL`（例如 `http://127.0.0.1:1234/v1`）和 `PACHAT_MODEL`（服务列出的确切模型 ID）。自定义服务须允许本机客户端无密钥访问，聊天程序只接受回环地址。

对“6 箱各 12 件、3 件损坏，能否承诺发货 70 件”这一问题，分析员用 4.95 秒算出可售 69 件、短缺 1 件。复核员读到正确答复后用 10.15 秒独立核对差额，明确同意结论。给它另一份声称“可售 75 件，可以承诺发货”的答复，它用 7.28 秒指出具体差错并纠正结论。两次检查均使用本机模型的短时推理，聊天界面只发布最终答复。耗时随上下文长度和机器负载而变化。
