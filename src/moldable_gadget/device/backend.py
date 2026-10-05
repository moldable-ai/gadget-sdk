"""Fixed-workspace, fixed-Bot bridge backend with durable outgoing intents."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable

from ..client import GadgetClient
from ..errors import GadgetError, PairingRequired
from ..protocol import Object, object_value, string
from ..storage import StateStore

Send = Callable[[Object], Awaitable[None]]


class BotBackend:
    def __init__(self, client: GadgetClient, workspace: str, bot: str, journal: StateStore) -> None:
        self.client, self.workspace, self.bot, self.journal = client, workspace, bot, journal
        self.active: Object | None = None
        self.cancel_requested = False

    async def text(self, text: str, send: Send) -> None:
        try:
            previous = self.journal.load()
        except PairingRequired:
            previous = {}
        if previous.get("pending"):
            raise GadgetError(
                "An earlier request has an uncertain outcome. Run bridge recover before sending."
            )
        intent: Object = {
            "pending": True,
            "mutation": str(uuid.uuid4()),
            "text": text,
            "workspace": self.workspace,
            "bot": self.bot,
        }
        self.journal.save(intent)
        await self._deliver(intent, send)

    async def recover(self, send: Send) -> None:
        intent = self.journal.load()
        if not intent.get("pending"):
            return
        if intent.get("workspace") != self.workspace or intent.get("bot") != self.bot:
            raise GadgetError("Recovery target differs from the stored request.")
        await self._deliver(intent, send)

    async def _deliver(self, intent: Object, send: Send) -> None:
        self.cancel_requested = False
        receipt = object_value(
            await self.client.send_text(
                self.workspace,
                self.bot,
                string(intent["text"], limit=24_000),
                mutation_id=string(intent["mutation"]),
            )
        )
        message = object_value(receipt.get("message"))
        turn = string(message.get("turnId"))
        thread = string(message.get("threadId"))
        intent.update({"turn": turn, "thread": thread})
        self.active = intent
        self.journal.save(intent)
        await send({"type": "turn.start", "turn": turn})
        cursor = string(object_value(receipt.get("event")).get("cursor"))
        try:
            async with asyncio.timeout(600):
                while True:
                    if self.cancel_requested:
                        await self.client.interrupt(
                            self.workspace, self.bot, turn, thread_id=thread
                        )
                        self.cancel_requested = False
                    page = await self.client.replay(self.workspace, self.bot, after_cursor=cursor)
                    if page.get("resetRequired"):
                        raise GadgetError("Conversation replay expired; check the desktop outcome.")
                    events = page.get("events")
                    if not isinstance(events, list):
                        raise GadgetError("Invalid conversation replay.")
                    for value in events:
                        event = object_value(value)
                        if event.get("sourceId") != "turn:" + turn and event.get("turnId") != turn:
                            continue
                        if event.get("type") == "message.assistant":
                            body = object_value(object_value(event.get("message")).get("body"))
                            await send(
                                {
                                    "type": "reply",
                                    "turn": turn,
                                    "text": string(body.get("text"), limit=128_000),
                                }
                            )
                        if event.get("type") == "turn.state" and event.get("state") in (
                            "completed",
                            "failed",
                            "cancelled",
                            "interrupted",
                        ):
                            state = event.get("state")
                            outcome = (
                                "success"
                                if state == "completed"
                                else "cancelled"
                                if state in ("cancelled", "interrupted")
                                else "failure"
                            )
                            intent["pending"] = False
                            self.journal.save(intent)
                            await send({"type": "turn.end", "turn": turn, "outcome": outcome})
                            return
                    next_cursor = page.get("nextCursor")
                    if next_cursor is not None:
                        next_cursor = string(next_cursor)
                        if page.get("hasMore") and next_cursor == cursor:
                            raise GadgetError("Replay cursor did not advance.")
                        cursor = next_cursor
                    if not page.get("hasMore"):
                        await asyncio.sleep(0.5)
        finally:
            self.active = None

    async def cancel(self) -> None:
        # The receiving loop remains responsive while append/replay is in flight.
        self.cancel_requested = True
        if self.active is not None:
            await self.client.interrupt(
                self.workspace,
                self.bot,
                string(self.active["turn"]),
                thread_id=string(self.active["thread"]),
            )
            self.cancel_requested = False
