from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


ROOT_DIR = Path(__file__).resolve().parents[2]
STATIC_DIR = Path(__file__).resolve().parent / "static"
WEB_RUNTIME_DIR = ROOT_DIR / ".runtime" / "web"
BOOTSTRAP_NODES_FILE = WEB_RUNTIME_DIR / "config" / "bootstrap_nodes.txt"
CONVERSATIONS_FILE = WEB_RUNTIME_DIR / "conversations.json"
MAX_CONTEXT_CHARS = 2400


def port_from_api_url(api_url: str) -> str:
    parsed = urlparse(api_url)
    return str(parsed.port) if parsed.port is not None else "?"


def read_available_nodes() -> list[dict]:
    if not BOOTSTRAP_NODES_FILE.exists():
        return []

    api_urls: list[str] = []
    for line in BOOTSTRAP_NODES_FILE.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            api_urls.append(stripped)

    nodes = []
    for index, api_url in enumerate(dict.fromkeys(api_urls)):
        port = port_from_api_url(api_url)
        nodes.append(
            {
                "peer_id": "",
                "port": port,
                "address": "",
                "api_url": api_url,
                "label": f"Bootstrap {index} | :{port}",
            }
        )
    return nodes


def load_conversations() -> list[dict]:
    if not CONVERSATIONS_FILE.exists():
        return []
    try:
        data = json.loads(CONVERSATIONS_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    conversations = [item for item in data if isinstance(item, dict)]
    conversations.sort(key=lambda item: float(item.get("updated_at", 0)), reverse=True)
    return conversations


def save_conversations(conversations: list[dict]) -> None:
    CONVERSATIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONVERSATIONS_FILE.write_text(
        json.dumps(conversations, indent=2),
        encoding="utf-8",
    )


def make_title(text: str) -> str:
    compact = " ".join(text.split())
    if len(compact) <= 34:
        return compact or "Untitled chat"
    return compact[:31].rstrip() + "..."


def find_conversation(conversations: list[dict], conversation_id: str) -> dict | None:
    for conversation in conversations:
        if str(conversation.get("id")) == conversation_id:
            return conversation
    return None


def ensure_conversation(
    conversations: list[dict],
    conversation_id: str | None,
    first_prompt: str,
) -> dict:
    if conversation_id:
        existing = find_conversation(conversations, conversation_id)
        if existing is not None:
            return existing

    now = time.time()
    conversation = {
        "id": str(uuid.uuid4()),
        "title": make_title(first_prompt),
        "created_at": now,
        "updated_at": now,
        "messages": [],
    }
    conversations.insert(0, conversation)
    return conversation


def build_network_prompt(messages: list[dict], prompt: str) -> str:
    if not messages:
        return prompt

    header = (
        "You are answering one turn in an ongoing UI chat. "
        "Use the recent context only if it is relevant.\n\n"
        "Recent context:\n"
    )
    footer = f"\nCurrent user message:\n{prompt}"
    budget = MAX_CONTEXT_CHARS - len(header) - len(footer)

    selected: list[str] = []
    used = 0
    for message in reversed(messages):
        role = str(message.get("role", "Assistant"))
        content = str(message.get("content", ""))
        block = f"{role}: {content}\n"
        if used + len(block) > budget:
            break
        selected.insert(0, block)
        used += len(block)

    if not selected:
        return prompt

    return header + "".join(selected) + footer


def summarize_conversations(conversations: list[dict]) -> list[dict]:
    return [
        {
            "id": conversation.get("id"),
            "title": conversation.get("title") or "Untitled chat",
            "updated_at": conversation.get("updated_at", 0),
        }
        for conversation in conversations
    ]


def query_node_api(
    node_api_url: str,
    prompt: str,
    timeout_s: float = 360.0,
) -> tuple[bool, str]:
    if not node_api_url:
        return False, "Selected node does not expose an HTTP API."

    url = node_api_url.rstrip("/") + "/api/query"
    body = json.dumps({"prompt": prompt}).encode("utf-8")
    request = Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urlopen(request, timeout=timeout_s) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raw = exc.read().decode("utf-8")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return False, raw or str(exc)
    except URLError as exc:
        return False, str(exc.reason)

    if not isinstance(payload, dict):
        return False, "Node API returned an invalid response."

    status = payload.get("status", "ok")
    answer = payload.get("answer")

    if status != "ok":
        return False, str(answer or status)

    return True, str(answer or "")
