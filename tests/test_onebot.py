"""用一个本地的假 NapCat（OneBot v11 正向 WebSocket 服务端）把收发全链路跑一遍。"""

import asyncio
import json

from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

from piao.bot.onebot import OneBotClient
from piao.bot.responder import Responder
from piao.bot.runtime import PiaoBot
from piao.config import BotConfig, Config


class ScriptedLLM:
    def __init__(self, reply):
        self.reply = reply
        self.prompts = []

    async def complete(self, system, user):
        self.prompts.append(user)
        return self.reply


def test_end_to_end_over_websocket():
    async def run():
        received: list[dict] = []
        auth_headers: list[str | None] = []

        async def napcat(ws):
            auth_headers.append(ws.request.headers.get("Authorization"))
            await ws.send(json.dumps({"post_type": "meta_event", "meta_event_type": "lifecycle", "self_id": 42}))
            await ws.send(
                json.dumps(
                    {
                        "post_type": "message",
                        "message_type": "group",
                        "self_id": 42,
                        "group_id": 100,
                        "user_id": 10001111,
                        "message_id": 1,
                        "time": 1760620000,
                        "sender": {"card": "张三"},
                        "message": [
                            {"type": "at", "data": {"qq": "42"}},
                            {"type": "text", "data": {"text": " 23466m345p11s5678p 切啥"}},
                        ],
                    }
                )
            )
            try:
                async for raw in ws:
                    req = json.loads(raw)
                    received.append(req)
                    resp = {"status": "ok", "retcode": 0, "data": {"message_id": 900 + len(received)}, "echo": req["echo"]}
                    await ws.send(json.dumps(resp))
            except ConnectionClosed:
                pass

        async with serve(napcat, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            cfg = Config(bot=BotConfig(groups=[100], debounce_seconds=0, typing_cps=1000))
            llm = ScriptedLLM("切5p\n没什么好说的")
            client = OneBotClient(f"ws://127.0.0.1:{port}", access_token="tok")
            bot = PiaoBot(cfg, client, Responder(llm, "SYSTEM", "轻轻的飘", "2044715303"))
            bot.typing_delay = lambda text, first: 0
            runner = asyncio.create_task(client.run_forever(bot.on_event))
            for _ in range(500):  # 等两条消息都发完并收到回执
                if len(bot.my_message_ids) == 2:
                    break
                await asyncio.sleep(0.01)
            runner.cancel()
        return received, auth_headers, llm, bot

    received, auth_headers, llm, bot = asyncio.run(run())
    assert auth_headers == ["Bearer tok"]
    assert [r["action"] for r in received] == ["send_group_msg", "send_group_msg"]
    first = received[0]["params"]
    assert first["group_id"] == 100
    assert first["message"][0] == {"type": "reply", "data": {"id": "1"}}
    assert first["message"][-1]["data"]["text"] == "切5p"
    assert received[1]["params"]["message"] == [{"type": "text", "data": {"text": "没什么好说的"}}]
    prompt = llm.prompts[0]
    assert "张三（@你）：@轻轻的飘  23466m345p11s5678p 切啥" in prompt
    assert "<牌效计算>" in prompt
    assert list(bot.my_message_ids) == ["901", "902"]
