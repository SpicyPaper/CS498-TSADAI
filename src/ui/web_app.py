from __future__ import annotations

import json
import argparse
import subprocess
import sys
import threading
import time
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse


ROOT_DIR = Path(__file__).resolve().parents[2]
STATIC_DIR = Path(__file__).resolve().parent / "static"
NETWORK_FILE = ROOT_DIR / ".runtime" / "state" / "network_nodes.txt"
BOOTSTRAP_FILE = ROOT_DIR / ".runtime" / "config" / "bootstrap_nodes.txt"
CONVERSATIONS_FILE = ROOT_DIR / ".runtime" / "ui" / "conversations.json"
MAX_CONTEXT_CHARS = 2400

LOCK = threading.Lock()


def peer_id_from_address(address: str) -> str:
    marker = "/p2p/"
    if marker not in address:
        return address
    return address.split(marker, 1)[1]


def port_from_address(address: str) -> str:
    parts = address.split("/")
    for index, part in enumerate(parts):
        if part == "tcp" and index + 1 < len(parts):
            return parts[index + 1]
    return "?"


def seed_bootstrap_file_from_local_network() -> None:
    if BOOTSTRAP_FILE.exists() or not NETWORK_FILE.exists():
        return

    for line in NETWORK_FILE.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[4] != "<unknown-yet>":
            BOOTSTRAP_FILE.parent.mkdir(parents=True, exist_ok=True)
            BOOTSTRAP_FILE.write_text(parts[4] + "\n", encoding="utf-8")
            return


def read_bootstrap_nodes() -> list[dict]:
    seed_bootstrap_file_from_local_network()

    if not BOOTSTRAP_FILE.exists():
        return []

    addresses: list[str] = []
    for line in BOOTSTRAP_FILE.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            addresses.append(stripped)

    nodes = []
    for address in dict.fromkeys(addresses):
        nodes.append(
            {
                "peer_id": peer_id_from_address(address),
                "port": port_from_address(address),
                "address": address,
                "label": f"Bootstrap | :{port_from_address(address)}",
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


def extract_client_answer(output: str) -> tuple[bool, str]:
    lines = [line.rstrip() for line in output.splitlines()]
    for index, line in enumerate(lines):
        if line == "QUERY OK":
            return True, "\n".join(lines[index + 1 :]).strip()
        if line == "QUERY FAILED":
            return False, "\n".join(lines[index + 1 :]).strip()
    return False, output.strip() or "No response received."


def run_query(entry_node: str, prompt: str) -> tuple[bool, str]:
    command = [
        sys.executable,
        "-m",
        "src.cli.client_query",
        "--entry-node",
        entry_node,
        "--prompt",
        prompt,
    ]
    result = subprocess.run(
        command,
        cwd=ROOT_DIR,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    output = "\n".join(part for part in [result.stdout, result.stderr] if part.strip())
    return extract_client_answer(output)


class WebAppHandler(BaseHTTPRequestHandler):
    server_version = "TSADAIWeb/0.1"

    def log_message(self, format: str, *args) -> None:
        return

    def do_GET(self) -> None:
        parsed = urlparse(self.path)

        if parsed.path == "/":
            self._send_file(STATIC_DIR / "index.html", "text/html; charset=utf-8")
            return

        if parsed.path == "/api/state":
            with LOCK:
                self._send_json(
                    {
                        "nodes": read_bootstrap_nodes(),
                        "conversations": summarize_conversations(load_conversations()),
                    }
                )
            return

        if parsed.path.startswith("/api/conversations/"):
            conversation_id = unquote(parsed.path.rsplit("/", 1)[-1])
            with LOCK:
                conversation = find_conversation(load_conversations(), conversation_id)
            if conversation is None:
                self._send_json({"error": "conversation not found"}, HTTPStatus.NOT_FOUND)
                return
            self._send_json(conversation)
            return

        if parsed.path.startswith("/static/"):
            relative = parsed.path.removeprefix("/static/")
            safe_name = Path(relative).name
            content_type = {
                ".css": "text/css; charset=utf-8",
                ".js": "application/javascript; charset=utf-8",
            }.get(Path(safe_name).suffix, "application/octet-stream")
            self._send_file(STATIC_DIR / safe_name, content_type)
            return

        self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)

        if parsed.path == "/api/conversations":
            with LOCK:
                conversations = load_conversations()
                conversation = ensure_conversation(conversations, None, "New chat")
                save_conversations(conversations)
            self._send_json(conversation)
            return

        if parsed.path == "/api/chat":
            body = self._read_json()
            entry_node = str(body.get("entry_node", "")).strip()
            prompt = str(body.get("prompt", "")).strip()
            conversation_id = body.get("conversation_id")

            if not entry_node or not prompt:
                self._send_json(
                    {"error": "entry_node and prompt are required"},
                    HTTPStatus.BAD_REQUEST,
                )
                return

            with LOCK:
                conversations = load_conversations()
                conversation = ensure_conversation(
                    conversations,
                    str(conversation_id) if conversation_id else None,
                    prompt,
                )
                messages = conversation.setdefault("messages", [])
                network_prompt = build_network_prompt(messages, prompt)
                messages.append(
                    {"role": "User", "content": prompt, "created_at": time.time()}
                )
                conversation["updated_at"] = time.time()
                save_conversations(conversations)

            try:
                ok, answer = run_query(entry_node, network_prompt)
            except subprocess.TimeoutExpired:
                ok, answer = False, "The query timed out after 30 seconds."
            except Exception as exc:
                ok, answer = False, str(exc)

            with LOCK:
                conversations = load_conversations()
                conversation = find_conversation(conversations, str(conversation["id"]))
                if conversation is not None and ok:
                    conversation.setdefault("messages", []).append(
                        {
                            "role": "Assistant",
                            "content": answer,
                            "created_at": time.time(),
                        }
                    )
                    conversation["updated_at"] = time.time()
                    conversations.sort(
                        key=lambda item: float(item.get("updated_at", 0)),
                        reverse=True,
                    )
                    save_conversations(conversations)

            self._send_json(
                {
                    "ok": ok,
                    "answer": answer,
                    "conversation_id": str(conversation_id or body.get("conversation_id") or ""),
                    "conversation": conversation,
                    "conversations": summarize_conversations(load_conversations()),
                },
                HTTPStatus.OK if ok else HTTPStatus.BAD_REQUEST,
            )
            return

        self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def do_DELETE(self) -> None:
        parsed = urlparse(self.path)
        if not parsed.path.startswith("/api/conversations/"):
            self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            return

        conversation_id = unquote(parsed.path.rsplit("/", 1)[-1])
        with LOCK:
            conversations = load_conversations()
            next_conversations = [
                item
                for item in conversations
                if str(item.get("id")) != conversation_id
            ]
            save_conversations(next_conversations)

        self._send_json({"ok": True, "conversations": summarize_conversations(next_conversations)})

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}

    def _send_json(self, data: object, status: HTTPStatus = HTTPStatus.OK) -> None:
        raw = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _send_file(self, path: Path, content_type: str) -> None:
        if not path.exists() or not path.is_file():
            self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            return

        raw = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def summarize_conversations(conversations: list[dict]) -> list[dict]:
    return [
        {
            "id": conversation.get("id"),
            "title": conversation.get("title") or "Untitled chat",
            "updated_at": conversation.get("updated_at", 0),
        }
        for conversation in conversations
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local TSADAI web UI.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), WebAppHandler)
    print(f"TSADAI web UI running at http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
