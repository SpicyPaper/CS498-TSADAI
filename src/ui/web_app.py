from __future__ import annotations

import argparse
import threading
import time

import uvicorn
from fastapi import FastAPI
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from src.ui.web_state import (
    STATIC_DIR,
    build_network_prompt,
    ensure_conversation,
    find_conversation,
    load_conversations,
    query_node_api,
    read_available_nodes,
    save_conversations,
    summarize_conversations,
)


app = FastAPI(title="TSADAI Gateway")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

lock = threading.Lock()
QUERY_TIMEOUT_S = 360.0


class ChatRequest(BaseModel):
    entry_node: str
    prompt: str
    conversation_id: str | None = None


class QueryRequest(BaseModel):
    entry_node: str
    prompt: str


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/state")
async def state() -> dict:
    with lock:
        return {
            "nodes": read_available_nodes(),
            "conversations": summarize_conversations(load_conversations()),
        }


@app.get("/api/conversations/{conversation_id}", response_model=None)
async def get_conversation(conversation_id: str):
    with lock:
        conversation = find_conversation(load_conversations(), conversation_id)

    if conversation is None:
        return JSONResponse(
            {"error": "conversation not found"},
            status_code=404,
        )

    return conversation


@app.post("/api/conversations")
async def create_conversation() -> dict:
    with lock:
        conversations = load_conversations()
        conversation = ensure_conversation(conversations, None, "New chat")
        save_conversations(conversations)
        return conversation


@app.delete("/api/conversations/{conversation_id}")
async def delete_conversation(conversation_id: str) -> dict:
    with lock:
        conversations = load_conversations()
        next_conversations = [
            item
            for item in conversations
            if str(item.get("id")) != conversation_id
        ]
        save_conversations(next_conversations)

    return {
        "ok": True,
        "conversations": summarize_conversations(next_conversations),
    }


@app.post("/api/query")
async def query(request: QueryRequest) -> dict:
    ok, answer = await run_in_threadpool(
        query_node_api,
        request.entry_node.strip(),
        request.prompt.strip(),
        QUERY_TIMEOUT_S,
    )
    return {"ok": ok, "answer": answer}


@app.post("/api/chat", response_model=None)
async def chat(request: ChatRequest):
    entry_node = request.entry_node.strip()
    prompt = request.prompt.strip()

    if not entry_node or not prompt:
        return JSONResponse(
            {"error": "entry_node and prompt are required"},
            status_code=400,
        )

    with lock:
        conversations = load_conversations()
        conversation = ensure_conversation(
            conversations,
            request.conversation_id,
            prompt,
        )
        messages = conversation.setdefault("messages", [])
        network_prompt = build_network_prompt(messages, prompt)
        messages.append(
            {"role": "User", "content": prompt, "created_at": time.time()}
        )
        conversation["updated_at"] = time.time()
        save_conversations(conversations)
        conversation_id = str(conversation["id"])

    try:
        ok, answer = await run_in_threadpool(
            query_node_api,
            entry_node,
            network_prompt,
            QUERY_TIMEOUT_S,
        )
    except TimeoutError:
        ok, answer = False, f"The query timed out after {QUERY_TIMEOUT_S:.0f} seconds."
    except Exception as exc:
        ok, answer = False, str(exc)

    with lock:
        conversations = load_conversations()
        conversation = find_conversation(conversations, conversation_id)

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

        response = {
            "ok": ok,
            "answer": answer,
            "error": None if ok else answer,
            "conversation_id": conversation_id,
            "conversation": conversation,
            "conversations": summarize_conversations(load_conversations()),
        }
        if not ok:
            return JSONResponse(response, status_code=400)
        return response


def main() -> None:
    global QUERY_TIMEOUT_S

    parser = argparse.ArgumentParser(description="Run the TSADAI web gateway.")
    parser.add_argument("--host", default="127.0.0.1", help="Web gateway host.")
    parser.add_argument("--port", type=int, default=8000, help="Web gateway port.")
    parser.add_argument(
        "--query-timeout",
        type=float,
        default=360.0,
        help="HTTP node API query timeout in seconds.",
    )
    args = parser.parse_args()
    QUERY_TIMEOUT_S = args.query_timeout

    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        reload=False,
    )


if __name__ == "__main__":
    main()
