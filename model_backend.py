"""OpenAI-compatible chat over loopback. Python standard library only."""

import ipaddress
import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit


DEFAULT_URL = "http://127.0.0.1:18765/v1"
DEFAULT_MODEL = "Qwen3-4B-Q4_K_M"
MODEL_HOME = Path.home() / ".cache" / "pachat-local"
KEY_FILE = MODEL_HOME / "model.key"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def loopback_url(url):
    parts = urlsplit(url)
    if (parts.scheme != "http" or parts.username is not None or parts.password is not None
            or parts.query or parts.fragment or parts.path.rstrip("/") != "/v1"):
        raise ValueError("模型地址需为本机 HTTP 服务的 /v1 路径")
    host = parts.hostname or ""
    if host == "localhost":
        host = "127.0.0.1"
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        raise ValueError("模型地址只接受本机回环 IP") from None
    if not address.is_loopback:
        raise ValueError("模型地址只接受本机回环 IP")
    authority = f"[{address}]" if address.version == 6 else str(address)
    return f"http://{authority}:{parts.port or 80}/v1"


def _transcript(history, budget):
    # Budget in UTF-8 bytes so even many uncommon characters fit the 8k context.
    lines = []
    left = budget
    for message in reversed(history):
        if message.get("kind") not in {"agent", "human"}:
            continue
        original = str(message.get("text", "")).encode()
        limit = min(3900, left)
        if limit < 96:
            break
        if len(original) > limit:
            half = (limit - 20) // 2
            content = original[:half].decode(errors="ignore") + "…（节选）…" + original[-half:].decode(errors="ignore")
        else:
            content = original.decode()
        who = str(message.get("author", ""))[:64]
        prefix = "用户" if message.get("kind") == "human" else "成员"
        line = f"[{prefix} · {who}]\n{content}"
        lines.append(line)
        left -= len(line.encode()) + 2
    return "\n\n".join(reversed(lines))


class ModelBackend:
    def __init__(self):
        self.url = loopback_url(os.environ.get("PACHAT_MODEL_URL", DEFAULT_URL))
        self.model = os.environ.get("PACHAT_MODEL", DEFAULT_MODEL)
        self.client = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect)

    def _request(self, path, payload=None, timeout=2):
        headers = {"Accept": "application/json"}
        if self.url == DEFAULT_URL and KEY_FILE.is_file():
            headers["Authorization"] = "Bearer " + KEY_FILE.read_text().strip()
        body = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(payload, ensure_ascii=False).encode()
        request = urllib.request.Request(self.url + path, data=body, headers=headers)
        with self.client.open(request, timeout=timeout) as response:
            return json.loads(response.read(1024 * 1024))

    def status(self):
        try:
            data = self._request("/models")["data"]
            if any(item.get("id") == self.model for item in data):
                return {"active_model": self.model, "model_status": "ready"}
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            pass
        return {"active_model": "", "model_status": "unavailable"}

    def reply(self, agent, history):
        latest = next((i for i in range(len(history) - 1, -1, -1)
                       if history[i].get("kind") == "human"), None)
        if latest is None:
            raise ValueError("没有可以回复的用户问题。")
        peers = _transcript(history[latest + 1:], 2100)
        question = _transcript(history[latest:latest + 1], 3200 if peers else 3900)
        previous = _transcript(history[:latest], 5700 - len((question + peers).encode()))
        role = agent.get("role") or "协助处理用户的问题"
        system = (f"你是本地群聊成员{agent['name']}，分工是{role}。"
                  "用与用户相同的语言发言，直接给出你的回复。一般控制在三四句话。"
                  "你只能读到下面的聊天记录。缺少资料时说明未知，不虚构文件、操作、检索或来源。")
        task = "请完成当前用户提出的任务。"
        if agent.get("id") == "reviewer":
            task = ("请审阅本轮其他成员的答复。先给复核结论（同意、需要修正或无法确认），"
                    "并明确指出你核对的是哪一项。再用原始条件做一个独立检查："
                    "涉及数字时可逆算或量化差距；涉及方案时可查找反例、核对假设。"
                    "已有结论正确时提供一条能验证它的依据，发现问题时给出修正。"
                    "用你自己的话简短发言，不重复原答，不虚构分歧。"
                    if peers else "请从复核员角度直接回答用户当前的问题。简要给出独立判断和核对依据。")
        sections = []
        if previous:
            sections.append("之前的群聊记录\n" + previous)
        sections.append("当前用户的原始问题\n" + question)
        if peers:
            sections.append("本轮其他成员的答复\n" + peers)
        sections.append("现在轮到你发言\n" + task)
        payload = {"model": self.model, "temperature": 0.35, "max_tokens": 500,
                   "messages": [{"role": "system", "content": system},
                                {"role": "user", "content": "\n\n".join(sections)}]}
        if self.url == DEFAULT_URL and self.model == DEFAULT_MODEL:
            # llama.cpp returns Qwen's working separately from its published reply.
            payload.update(chat_template_kwargs={"enable_thinking": True},
                           reasoning_format="deepseek", reasoning_budget=640, max_tokens=1250)
        try:
            response = self._request("/chat/completions", payload, timeout=180)
            choice = response["choices"][0]
            content = choice["message"]["content"]
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"本机模型返回 HTTP {exc.code}，请检查模型状态后重试。") from None
        except (OSError, ValueError, KeyError, IndexError, TypeError):
            raise RuntimeError("无法取得本机模型回复，请确认本地模型服务已启动。") from None
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError("本机模型返回了空白回复，请重试。")
        content = content.strip()
        if choice.get("finish_reason") == "length":
            content += "\n\n（本次回复已达到长度上限。）"
        return content
