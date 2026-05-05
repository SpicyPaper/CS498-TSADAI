from __future__ import annotations

import uvicorn
import trio
from fastapi import FastAPI
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from src.logging_utils import log
from src.node import Node


class NodeQueryRequest(BaseModel):
    prompt: str
    query_id: str | None = None
    required_capabilities: dict[str, float] | None = None


class NodeAPIService:
    def __init__(self, node: Node, host: str, port: int) -> None:
        self.node = node
        self.host = host
        self.port = port
        self.app = FastAPI(title="TSADAI Node API")
        self.app.add_api_route(
            "/api/query",
            self.query,
            methods=["POST"],
            response_model=None,
        )

    async def run(self) -> None:
        config = uvicorn.Config(
            self.app,
            host=self.host,
            port=self.port,
            log_level="warning",
        )
        server = uvicorn.Server(config)

        log("HTTP", f"Node API listening on http://{self.host}:{self.port}")
        log("HTTP", f"Query endpoint: POST http://{self.host}:{self.port}/api/query")
        try:
            await trio.to_thread.run_sync(server.run, abandon_on_cancel=True)
        finally:
            server.should_exit = True

    async def query(self, request: NodeQueryRequest):
        if not request.prompt.strip():
            return JSONResponse(
                {"error": "prompt must be a non-empty string"},
                status_code=400,
            )

        try:
            reply = await run_in_threadpool(
                self.node.answer_query_from_api,
                request.prompt,
                request.query_id,
                request.required_capabilities,
            )
        except Exception as exc:
            log("HTTP", f"Query failed: {exc}")
            return JSONResponse(
                {"error": str(exc)},
                status_code=500,
            )

        return reply
