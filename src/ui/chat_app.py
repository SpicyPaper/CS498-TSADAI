from __future__ import annotations

import queue
import json
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from tkinter import BOTH, END, LEFT, RIGHT, X, Y
from tkinter import Button, Entry, Frame, Label, Listbox, StringVar, Text, Tk
from tkinter import font, ttk


ROOT_DIR = Path(__file__).resolve().parents[2]
NETWORK_FILE = ROOT_DIR / ".network" / "network_nodes.txt"
BOOTSTRAP_FILE = ROOT_DIR / ".network" / "bootstrap_nodes.txt"
CONVERSATIONS_FILE = ROOT_DIR / ".network" / "ui_conversations.json"
MAX_CONTEXT_CHARS = 2400


@dataclass(frozen=True)
class NetworkNode:
    peer_id: str
    port: str
    address: str

    @property
    def label(self) -> str:
        return f"Bootstrap  |  :{self.port}"


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


def read_bootstrap_addresses() -> list[str]:
    seed_bootstrap_file_from_local_network()

    if not BOOTSTRAP_FILE.exists():
        return []

    addresses: list[str] = []
    for line in BOOTSTRAP_FILE.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        addresses.append(stripped)

    return list(dict.fromkeys(addresses))


def seed_bootstrap_file_from_local_network() -> None:
    if BOOTSTRAP_FILE.exists() or not NETWORK_FILE.exists():
        return

    for line in NETWORK_FILE.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[4] != "<unknown-yet>":
            BOOTSTRAP_FILE.parent.mkdir(parents=True, exist_ok=True)
            BOOTSTRAP_FILE.write_text(parts[4] + "\n", encoding="utf-8")
            return


def load_network_nodes() -> list[NetworkNode]:
    nodes: list[NetworkNode] = []
    for address in read_bootstrap_addresses():
        nodes.append(
            NetworkNode(
                peer_id=peer_id_from_address(address),
                port=port_from_address(address),
                address=address,
            )
        )
    return nodes


def legacy_network_nodes() -> list[NetworkNode]:
    if not NETWORK_FILE.exists():
        return []

    nodes: list[NetworkNode] = []
    for line in NETWORK_FILE.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[4] != "<unknown-yet>":
            nodes.append(
                NetworkNode(
                    peer_id=peer_id_from_address(parts[4]),
                    port=parts[1],
                    address=parts[4],
                )
            )
    return nodes


def extract_client_answer(output: str) -> tuple[bool, str]:
    lines = [line.rstrip() for line in output.splitlines()]
    for idx, line in enumerate(lines):
        if line == "QUERY OK":
            return True, "\n".join(lines[idx + 1 :]).strip()
        if line == "QUERY FAILED":
            return False, "\n".join(lines[idx + 1 :]).strip()
    return False, output.strip() or "No response received."


class ChatApp:
    def __init__(self, root: Tk) -> None:
        self.root = root
        self.root.title("TSADAI Chat")
        self.root.geometry("860x620")
        self.root.minsize(640, 460)

        self.nodes: list[NetworkNode] = []
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.is_sending = False
        self.auto_refresh_ms = 3000
        self.conversations: list[dict] = []
        self.current_conversation_id: str | None = None

        self.selected_node = StringVar()
        self.status_text = StringVar(value="Start the network, then refresh nodes.")

        self._configure_style()
        self._load_conversations()
        self._build_layout()
        self._refresh_conversation_list()
        self.refresh_nodes(show_status=True)
        self.root.after(100, self._drain_events)
        self.root.after(self.auto_refresh_ms, self._auto_refresh_nodes)

    def _configure_style(self) -> None:
        self.root.configure(bg="#f5f7fb")

        default_font = font.nametofont("TkDefaultFont")
        default_font.configure(family="Segoe UI", size=10)
        text_font = font.nametofont("TkTextFont")
        text_font.configure(family="Segoe UI", size=10)

        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TCombobox", padding=5)

    def _build_layout(self) -> None:
        shell = Frame(self.root, bg="#f5f7fb")
        shell.pack(fill=BOTH, expand=True)

        sidebar = Frame(shell, width=220, bg="#eef2f7", padx=10, pady=12)
        sidebar.pack(side=LEFT, fill=Y)
        sidebar.pack_propagate(False)

        Label(
            sidebar,
            text="Conversations",
            bg="#eef2f7",
            fg="#111827",
            font=("Segoe UI", 12, "bold"),
        ).pack(anchor="w")

        Button(sidebar, text="New chat", command=self.clear_chat).pack(fill=X, pady=(10, 6))
        Button(sidebar, text="Delete chat", command=self.delete_selected_conversation).pack(
            fill=X, pady=(0, 8)
        )

        self.conversation_list = Listbox(
            sidebar,
            relief="flat",
            borderwidth=0,
            activestyle="none",
            bg="#ffffff",
            fg="#111827",
            selectbackground="#dbeafe",
            selectforeground="#111827",
            highlightthickness=0,
        )
        self.conversation_list.pack(fill=BOTH, expand=True)
        self.conversation_list.bind("<<ListboxSelect>>", self._select_conversation)

        main = Frame(shell, bg="#f5f7fb")
        main.pack(side=LEFT, fill=BOTH, expand=True)

        top_bar = Frame(main, padx=14, pady=12, bg="#f5f7fb")
        top_bar.pack(fill=X)

        title = Label(
            top_bar,
            text="TSADAI Chat",
            bg="#f5f7fb",
            fg="#111827",
            font=("Segoe UI", 14, "bold"),
        )
        title.pack(side=LEFT, padx=(0, 18))

        Label(top_bar, text="Entry node", bg="#f5f7fb", fg="#374151").pack(side=LEFT)

        self.node_picker = ttk.Combobox(
            top_bar,
            textvariable=self.selected_node,
            width=34,
            state="readonly",
        )
        self.node_picker.pack(side=LEFT, padx=(8, 8))

        Button(top_bar, text="Refresh", command=lambda: self.refresh_nodes(show_status=True)).pack(side=LEFT)
        Label(top_bar, textvariable=self.status_text, anchor="e", bg="#f5f7fb", fg="#6b7280").pack(
            side=RIGHT, fill=X, expand=True
        )

        self.chat = Text(
            main,
            wrap="word",
            padx=18,
            pady=16,
            state="disabled",
            bg="#ffffff",
            fg="#111827",
            relief="flat",
            borderwidth=0,
            insertbackground="#111827",
        )
        self.chat.pack(in_=main, fill=BOTH, expand=True, padx=14, pady=(0, 0))
        self.chat.tag_configure("user", foreground="#174ea6", font=("Segoe UI", 10, "bold"), spacing3=8)
        self.chat.tag_configure("assistant", foreground="#166534", spacing3=12)
        self.chat.tag_configure("system", foreground="#6b7280", spacing3=8)
        self.chat.tag_configure("error", foreground="#b42318", spacing3=8)

        input_bar = Frame(main, padx=14, pady=14, bg="#f5f7fb")
        input_bar.pack(fill=X)

        self.prompt_entry = Entry(input_bar, relief="flat", borderwidth=8)
        self.prompt_entry.pack(side=LEFT, fill=X, expand=True)
        self.prompt_entry.bind("<Return>", lambda _event: self.send_prompt())

        self.send_button = Button(
            input_bar,
            text="Send",
            command=self.send_prompt,
            bg="#2563eb",
            fg="#ffffff",
            activebackground="#1d4ed8",
            activeforeground="#ffffff",
            relief="flat",
            padx=16,
            pady=4,
        )
        self.send_button.pack(side=RIGHT, padx=(8, 0))

        self._append(
            "system",
            "Start a new chat or select a saved conversation. The network receives a compact local context for the active chat.",
        )

    def refresh_nodes(self, show_status: bool = False) -> None:
        previous = self.selected_node.get()
        self.nodes = load_network_nodes()
        labels = [node.label for node in self.nodes]
        self.node_picker["values"] = labels

        if labels:
            if previous in labels:
                self.selected_node.set(previous)
            else:
                self.selected_node.set(labels[0])
            suffix = " available"
            self.status_text.set(f"{len(labels)} node(s){suffix}")
        else:
            self.selected_node.set("")
            self.status_text.set(f"No nodes found in {BOOTSTRAP_FILE}")

        if show_status and labels:
            self._append(
                "system",
                f"Loaded {len(labels)} bootstrap node(s).",
            )

    def _auto_refresh_nodes(self) -> None:
        current_labels = [node.label for node in self.nodes]
        latest_nodes = load_network_nodes()
        latest_labels = [node.label for node in latest_nodes]

        if latest_labels != current_labels:
            self.refresh_nodes(show_status=False)
            self._append("system", f"Network list updated: {len(latest_labels)} node(s) available.")

        self.root.after(self.auto_refresh_ms, self._auto_refresh_nodes)

    def _load_conversations(self) -> None:
        if not CONVERSATIONS_FILE.exists():
            self.conversations = []
            return

        try:
            data = json.loads(CONVERSATIONS_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            self.conversations = []
            return

        if isinstance(data, list):
            self.conversations = [item for item in data if isinstance(item, dict)]
        else:
            self.conversations = []

        self.conversations.sort(
            key=lambda item: float(item.get("updated_at", 0)),
            reverse=True,
        )

    def _save_conversations(self) -> None:
        CONVERSATIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
        CONVERSATIONS_FILE.write_text(
            json.dumps(self.conversations, indent=2),
            encoding="utf-8",
        )

    def _refresh_conversation_list(self) -> None:
        self.conversation_list.delete(0, END)
        selected_index = None

        for index, conversation in enumerate(self.conversations):
            title = conversation.get("title") or "Untitled chat"
            self.conversation_list.insert(END, title)
            if conversation.get("id") == self.current_conversation_id:
                selected_index = index

        if selected_index is not None:
            self.conversation_list.selection_set(selected_index)
            self.conversation_list.see(selected_index)

    def _select_conversation(self, _event=None) -> None:
        selection = self.conversation_list.curselection()
        if not selection:
            return

        conversation = self.conversations[selection[0]]
        self.current_conversation_id = str(conversation.get("id"))
        self._render_current_conversation()

    def _get_conversation(self, conversation_id: str | None) -> dict | None:
        if conversation_id is None:
            return None

        for conversation in self.conversations:
            if conversation.get("id") == conversation_id:
                return conversation
        return None

    def _ensure_current_conversation(self, first_prompt: str) -> dict:
        conversation = self._get_conversation(self.current_conversation_id)
        if conversation is not None:
            return conversation

        now = time.time()
        conversation = {
            "id": str(uuid.uuid4()),
            "title": self._make_title(first_prompt),
            "created_at": now,
            "updated_at": now,
            "messages": [],
        }
        self.conversations.insert(0, conversation)
        self.current_conversation_id = conversation["id"]
        self._save_conversations()
        self._refresh_conversation_list()
        return conversation

    def _make_title(self, text: str) -> str:
        compact = " ".join(text.split())
        if len(compact) <= 34:
            return compact or "Untitled chat"
        return compact[:31].rstrip() + "..."

    def _conversation_messages(self, conversation_id: str | None = None) -> list[dict]:
        conversation = self._get_conversation(
            conversation_id or self.current_conversation_id
        )
        if conversation is None:
            return []

        messages = conversation.get("messages", [])
        if isinstance(messages, list):
            return [message for message in messages if isinstance(message, dict)]
        return []

    def _add_message(self, conversation_id: str, role: str, content: str) -> None:
        conversation = self._get_conversation(conversation_id)
        if conversation is None:
            return

        messages = conversation.setdefault("messages", [])
        messages.append(
            {
                "role": role,
                "content": content,
                "created_at": time.time(),
            }
        )
        conversation["updated_at"] = time.time()

        if role == "User" and len(messages) == 1:
            conversation["title"] = self._make_title(content)

        self.conversations.sort(
            key=lambda item: float(item.get("updated_at", 0)),
            reverse=True,
        )
        self._save_conversations()
        self._refresh_conversation_list()

    def _render_current_conversation(self) -> None:
        self.chat.configure(state="normal")
        self.chat.delete("1.0", END)
        self.chat.configure(state="disabled")

        messages = self._conversation_messages()
        if not messages:
            self._append(
                "system",
                "New chat started. Send a message to create a saved conversation.",
            )
            return

        for message in messages:
            role = str(message.get("role", "Assistant"))
            content = str(message.get("content", ""))
            if role == "User":
                self._append("user", f"You: {content}")
            else:
                self._append("assistant", f"TSADAI: {content}")

    def send_prompt(self) -> None:
        prompt = self.prompt_entry.get().strip()
        if not prompt or self.is_sending:
            return

        node = self._current_node()
        if node is None:
            self._append("system", "No entry node selected. Start the network and refresh.")
            return

        conversation = self._ensure_current_conversation(prompt)
        conversation_id = str(conversation["id"])
        network_prompt = self._build_network_prompt(prompt)

        self.prompt_entry.delete(0, END)
        self._append("user", f"You: {prompt}")
        self._add_message(conversation_id, "User", prompt)
        self._set_sending(True)

        thread = threading.Thread(
            target=self._query_in_background,
            args=(node.address, conversation_id, network_prompt),
            daemon=True,
        )
        thread.start()

    def _build_network_prompt(self, prompt: str) -> str:
        messages = self._conversation_messages()
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

    def _current_node(self) -> NetworkNode | None:
        selected = self.selected_node.get()
        for node in self.nodes:
            if node.label == selected:
                return node
        return None

    def _query_in_background(
        self, entry_node: str, conversation_id: str, network_prompt: str
    ) -> None:
        command = [
            sys.executable,
            "-m",
            "src.cli.client_query",
            "--entry-node",
            entry_node,
            "--prompt",
            network_prompt,
        ]

        try:
            result = subprocess.run(
                command,
                cwd=ROOT_DIR,
                text=True,
                capture_output=True,
                timeout=30,
                check=False,
            )
            output = "\n".join(
                part for part in [result.stdout, result.stderr] if part.strip()
            )
            ok, answer = extract_client_answer(output)
            payload = {"conversation_id": conversation_id, "content": answer}
            self.events.put(("answer" if ok else "error", payload))
        except subprocess.TimeoutExpired:
            self.events.put(("error", "The query timed out after 30 seconds."))
        except Exception as exc:
            self.events.put(("error", str(exc)))
        finally:
            self.events.put(("sending", False))

    def _drain_events(self) -> None:
        while True:
            try:
                event_type, payload = self.events.get_nowait()
            except queue.Empty:
                break

            if event_type == "sending":
                self._set_sending(bool(payload))
            elif event_type == "answer":
                if not isinstance(payload, dict):
                    continue
                conversation_id = str(payload.get("conversation_id", ""))
                content = str(payload.get("content", ""))
                self._add_message(conversation_id, "Assistant", content)
                if conversation_id == self.current_conversation_id:
                    self._append("assistant", f"TSADAI: {content}")
                self.refresh_nodes(show_status=False)
            elif event_type == "error":
                if isinstance(payload, dict):
                    content = str(payload.get("content", ""))
                else:
                    content = str(payload)
                self._append("error", f"Error: {content}")

        self.root.after(100, self._drain_events)

    def _set_sending(self, value: bool) -> None:
        self.is_sending = value
        state = "disabled" if value else "normal"
        self.send_button.configure(state=state)
        self.prompt_entry.configure(state=state)
        self.status_text.set("Waiting for network response..." if value else "Ready")

    def _append(self, tag: str, message: str) -> None:
        self.chat.configure(state="normal")
        self.chat.insert(END, message.strip() + "\n\n", tag)
        self.chat.configure(state="disabled")
        self.chat.see(END)

    def clear_chat(self) -> None:
        self.current_conversation_id = None
        self.conversation_list.selection_clear(0, END)
        self.chat.configure(state="normal")
        self.chat.delete("1.0", END)
        self.chat.configure(state="disabled")
        self._append(
            "system",
            "New chat started. Send a message to create a saved conversation.",
        )

    def delete_selected_conversation(self) -> None:
        selection = self.conversation_list.curselection()
        if not selection:
            self._append("system", "Select a conversation to delete.")
            return

        conversation = self.conversations[selection[0]]
        conversation_id = str(conversation.get("id"))
        self.conversations = [
            item
            for item in self.conversations
            if str(item.get("id")) != conversation_id
        ]
        self._save_conversations()

        if self.current_conversation_id == conversation_id:
            self.current_conversation_id = None
            self.chat.configure(state="normal")
            self.chat.delete("1.0", END)
            self.chat.configure(state="disabled")
            self._append("system", "Conversation deleted.")

        self._refresh_conversation_list()


def main() -> None:
    root = Tk()
    ChatApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
