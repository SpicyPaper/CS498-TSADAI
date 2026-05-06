from __future__ import annotations

import argparse
import asyncio
import threading
import time
import uuid

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
QUERY_TIMEOUT_S = 150.0
QUERY_STATUSES: dict[str, dict] = {}


class ChatRequest(BaseModel):
    entry_node: str
    prompt: str
    conversation_id: str | None = None


class QueryRequest(BaseModel):
    entry_node: str
    prompt: str


def _query_status_message(elapsed_s: float, done: bool) -> str:
    if done:
        return "Network response received."
    if elapsed_s < 3:
        return "Sending the request to the selected entry node..."
    if elapsed_s < 10:
        return "The entry node is routing the request through the network..."
    if elapsed_s < 60:
        return "Waiting for the selected node to generate an answer..."
    return "Still waiting. If the selected node fails, the entry node can try another candidate."


def _set_query_status(query_id: str, **updates) -> None:
    with lock:
        status = QUERY_STATUSES.setdefault(
            query_id,
            {
                "query_id": query_id,
                "started_at": time.time(),
                "done": False,
                "ok": None,
                "message": "Preparing request...",
            },
        )
        status.update(updates)


def _finish_chat_query(
    query_id: str,
    conversation_id: str,
    entry_node: str,
    network_prompt: str,
) -> None:
    _set_query_status(
        query_id,
        phase="waiting_for_network",
        message="Waiting for the network response...",
    )

    try:
        ok, answer, routing_trace = query_node_api(
            entry_node,
            network_prompt,
            QUERY_TIMEOUT_S,
        )
    except TimeoutError:
        ok, answer, routing_trace = (
            False,
            f"The query timed out after {QUERY_TIMEOUT_S:.0f} seconds.",
            None,
        )
    except Exception as exc:
        ok, answer, routing_trace = False, str(exc), None

    with lock:
        conversations = load_conversations()
        conversation = find_conversation(conversations, conversation_id)

        if conversation is not None and ok:
            conversation.setdefault("messages", []).append(
                {
                    "role": "Assistant",
                    "content": answer,
                    "routing_trace": routing_trace,
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
            "routing_trace": routing_trace,
            "error": None if ok else answer,
            "conversation_id": conversation_id,
            "conversation": conversation,
            "conversations": summarize_conversations(load_conversations()),
        }

        status = QUERY_STATUSES.setdefault(query_id, {"query_id": query_id})
        status.update(
            {
                "done": True,
                "ok": ok,
                "phase": "done" if ok else "error",
                "message": "Network response received." if ok else str(answer),
                "response": response,
                "finished_at": time.time(),
            }
        )


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
    ok, answer, routing_trace = await run_in_threadpool(
        query_node_api,
        request.entry_node.strip(),
        request.prompt.strip(),
        QUERY_TIMEOUT_S,
    )
    return {"ok": ok, "answer": answer, "routing_trace": routing_trace}


@app.post("/api/chat/start", response_model=None)
async def start_chat(request: ChatRequest):
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

    query_id = str(uuid.uuid4())
    _set_query_status(
        query_id,
        conversation_id=conversation_id,
        phase="queued",
        message="Preparing request...",
    )
    threading.Thread(
        target=_finish_chat_query,
        args=(query_id, conversation_id, entry_node, network_prompt),
        daemon=True,
    ).start()

    return {
        "ok": True,
        "query_id": query_id,
        "conversation_id": conversation_id,
        "conversation": conversation,
        "conversations": summarize_conversations(load_conversations()),
    }


@app.get("/api/chat/status/{query_id}", response_model=None)
async def chat_status(query_id: str):
    with lock:
        status = dict(QUERY_STATUSES.get(query_id, {}))

    if not status:
        return JSONResponse({"error": "query not found"}, status_code=404)

    elapsed_s = time.time() - float(status.get("started_at", time.time()))
    done = bool(status.get("done"))
    status["elapsed_s"] = elapsed_s
    status["message"] = status.get("message") or _query_status_message(
        elapsed_s,
        done,
    )
    if not done:
        status["message"] = _query_status_message(elapsed_s, done)
    return status


@app.post("/api/chat", response_model=None)
async def chat(request: ChatRequest):
    start_response = await start_chat(request)
    query_id = start_response["query_id"]
    deadline = time.time() + QUERY_TIMEOUT_S

    while time.time() < deadline:
        with lock:
            status = QUERY_STATUSES.get(query_id)
            if status and status.get("done"):
                response = status["response"]
                if not response.get("ok"):
                    return JSONResponse(response, status_code=400)
                return response
        await asyncio.sleep(0.25)

    response = {
        "ok": False,
        "answer": f"The query timed out after {QUERY_TIMEOUT_S:.0f} seconds.",
        "error": f"The query timed out after {QUERY_TIMEOUT_S:.0f} seconds.",
        "conversation_id": start_response["conversation_id"],
        "conversation": start_response["conversation"],
        "conversations": summarize_conversations(load_conversations()),
    }
    return JSONResponse(response, status_code=400)


def main() -> None:
    global QUERY_TIMEOUT_S

    parser = argparse.ArgumentParser(description="Run the TSADAI web gateway.")
    parser.add_argument("--host", default="127.0.0.1", help="Web gateway host.")
    parser.add_argument("--port", type=int, default=8000, help="Web gateway port.")
    parser.add_argument(
        "--query-timeout",
        type=float,
        default=150.0,
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
