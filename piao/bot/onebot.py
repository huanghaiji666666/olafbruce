"""极简 OneBot v11 正向 WebSocket 客户端（NapCat / Lagrange 等都兼容）。"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import WebSocketException

log = logging.getLogger(__name__)

EventHandler = Callable[[dict[str, Any]], Awaitable[None]]


class ActionError(RuntimeError):
    pass


class OneBotClient:
    def __init__(self, url: str, access_token: str = "", timeout: float = 20.0):
        self.url = url
        self.headers = {"Authorization": f"Bearer {access_token}"} if access_token else {}
        self.timeout = timeout
        self._ws: ClientConnection | None = None
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._tasks: set[asyncio.Task[None]] = set()

    async def call(self, action: str, **params: Any) -> dict[str, Any]:
        if self._ws is None:
            raise ActionError("还没有连上 OneBot")
        echo = uuid.uuid4().hex
        fut: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[echo] = fut
        try:
            await self._ws.send(json.dumps({"action": action, "params": params, "echo": echo}, ensure_ascii=False))
            resp = await asyncio.wait_for(fut, self.timeout)
        finally:
            self._pending.pop(echo, None)
        if resp.get("status") == "failed" or resp.get("retcode", 0) != 0:
            raise ActionError(f"{action} 失败: {resp.get('message') or resp.get('wording') or resp}")
        return resp.get("data") or {}

    def _dispatch(self, handler: EventHandler, event: dict[str, Any]) -> None:
        task = asyncio.create_task(handler(event))
        self._tasks.add(task)

        def done(t: asyncio.Task[None]) -> None:
            self._tasks.discard(t)
            if not t.cancelled() and t.exception():
                log.error("处理事件出错", exc_info=t.exception())

        task.add_done_callback(done)

    async def run_forever(self, handler: EventHandler) -> None:
        delay = 1.0
        while True:
            try:
                async with connect(self.url, additional_headers=self.headers, max_size=None) as ws:
                    self._ws = ws
                    delay = 1.0
                    log.info("已连接 OneBot: %s", self.url)
                    async for raw in ws:
                        try:
                            data = json.loads(raw)
                        except json.JSONDecodeError:
                            continue
                        echo = data.get("echo")
                        if echo and echo in self._pending:
                            fut = self._pending[echo]
                            if not fut.done():
                                fut.set_result(data)
                        elif "post_type" in data:
                            self._dispatch(handler, data)
            except (OSError, WebSocketException) as e:
                log.warning("OneBot 连接断开（%s），%.0f 秒后重连", e, delay)
            finally:
                self._ws = None
                for fut in self._pending.values():
                    if not fut.done():
                        fut.set_exception(ActionError("连接已断开"))
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60)
