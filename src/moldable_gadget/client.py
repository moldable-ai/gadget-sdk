"""Async client for a trusted gadget using the existing Moldable controller API."""

from __future__ import annotations

import asyncio
import os
import uuid
from collections import OrderedDict
from collections.abc import AsyncIterator
from contextlib import suppress
from pathlib import Path

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

from .errors import (
    ConnectionLost,
    GadgetError,
    PairingRequired,
    ProtocolError,
    RemoteError,
    ReplayRequired,
)
from .pairing import refresh, validate_state
from .protocol import (
    Json,
    Object,
    RoomCipher,
    b64,
    connect_proof,
    dumps,
    loads,
    object_value,
    routing,
    string,
    verify,
)
from .storage import StateStore, default_state_directory


class GadgetClient:
    """One connection and credential writer. Reconnect never resends mutations.

    Use an async context manager. Calls require explicit workspace and Bot IDs;
    no request depends on the desktop's currently selected workspace.
    """

    def __init__(
        self,
        state_dir: str | Path | None = None,
        *,
        timeout: float = 30,
        allow_insecure_localhost: bool = False,
    ) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self.store = StateStore(state_dir or default_state_directory())
        self.timeout = timeout
        self.local = allow_insecure_localhost
        self.ready: Object = {}
        self._state: Object = {}
        self._socket: ClientConnection | None = None
        self._reader: asyncio.Task[None] | None = None
        self._pending: dict[str, asyncio.Future[Json]] = {}
        self._events: asyncio.Queue[Object | GadgetError] = asyncio.Queue(maxsize=128)
        self._seen: OrderedDict[str, None] = OrderedDict()
        self._sequence: int | None = None
        self._force_refresh = False
        self._failure: GadgetError | None = None
        self._ready_event = asyncio.Event()
        self._hello_nonce = ""
        self._cipher: RoomCipher | None = None
        self._lifecycle = asyncio.Lock()
        self._workspace: str | None = None
        self._collect_events = False

    async def __aenter__(self) -> GadgetClient:
        self.store.__enter__()
        try:
            await self.reconnect()
        except BaseException:
            await self.close()
            raise
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def close(self) -> None:
        async with self._lifecycle:
            await self._disconnect()
            self.store.__exit__()

    async def _disconnect(self) -> None:
        if self._reader:
            self._reader.cancel()
            with suppress(asyncio.CancelledError):
                await self._reader
            self._reader = None
        if self._socket:
            await self._socket.close()
            self._socket = None
        self._fail(ConnectionLost("Connection closed; a sent request may have completed."))

    async def reconnect(self) -> None:
        """Reconnect and refresh if needed. Caller must reconcile missed events."""
        async with self._lifecycle:
            await self._disconnect()
            self._state = await refresh(
                self.store, self.store.load(), force=self._force_refresh, local=self.local
            )
            self._force_refresh = False
            validate_state(self._state, local=self.local)
            self._cipher = RoomCipher(
                string(self._state["e2eeKey"]),
                string(self._state["desktopId"]),
                string(self._state["desktopPublicKey"]),
            )
            self._failure = None
            self._events = asyncio.Queue(maxsize=128)
            self._sequence = None
            self.ready = {}
            self._ready_event.clear()
            try:
                for attempt in range(2):
                    try:
                        self._socket = await connect(
                            string(self._state["websocketURL"]),
                            additional_headers={
                                "Authorization": "Bearer " + string(self._state["accessToken"])
                            },
                            open_timeout=self.timeout,
                            close_timeout=5,
                            max_size=1024 * 1024,
                            max_queue=16,
                            ping_interval=20,
                            ping_timeout=20,
                            compression=None,
                            proxy=None,
                        )
                        break
                    except InvalidStatus as error:
                        if error.response.status_code != 401:
                            raise
                        if attempt:
                            self.store.forget()
                            raise PairingRequired(
                                "Relay rejected the refreshed pairing."
                            ) from error
                        # An offline revocation can reject the HTTP upgrade before
                        # the WebSocket can carry access.revoked. Refresh distinguishes
                        # a stale access token from a revoked grant; no mutation is sent.
                        self._state = await refresh(
                            self.store, self._state, force=True, local=self.local
                        )
                        validate_state(self._state, local=self.local)
                async with asyncio.timeout(self.timeout):
                    await self._authenticate()
                    self._hello_nonce = b64(os.urandom(32))
                    self._reader = asyncio.create_task(self._receive())
                    await self._request("remote.hello", {"clientNonce": self._hello_nonce})
                    await self._ready_event.wait()
                    if self._failure:
                        raise self._failure
                    if not self.ready:
                        raise ProtocolError("Desktop did not return a verified ready event.")
                    if self._workspace is not None:
                        await self.select_workspace(self._workspace)
            except BaseException:
                await self._disconnect()
                raise

    async def _authenticate(self) -> None:
        assert self._socket is not None
        challenge = object_value(loads(await self._socket.recv()))
        if challenge.get("event") == "access.revoked":
            self.store.forget()
            raise PairingRequired("Device access was revoked.")
        if challenge.get("type") != "event" or challenge.get("event") != "connect.challenge":
            raise ProtocolError("Relay did not send a connection challenge.")
        nonce = routing(object_value(challenge.get("params")).get("nonce"))
        request_id = "connect-" + str(uuid.uuid4())
        await self._socket.send(
            dumps(
                {
                    "type": "req",
                    "id": request_id,
                    "method": "connect",
                    "params": connect_proof(
                        nonce,
                        string(self._state["sessionId"]),
                        string(self._state["deviceId"]),
                        string(self._state["privateKey"]),
                    ),
                }
            )
        )
        reply = object_value(loads(await self._socket.recv()))
        if reply.get("id") != request_id or reply.get("type") != "res":
            raise ProtocolError("Unexpected Relay authentication response.")
        if "error" in reply:
            raise self._remote_error(object_value(reply["error"]))
        if object_value(reply.get("result")).get("protocol") != 2:
            raise ProtocolError("Relay did not negotiate protocol v2.")

    async def _request(self, method: str, params: Object) -> Json:
        if self._failure:
            raise self._failure
        if self._socket is None or self._cipher is None:
            raise ConnectionLost("Client is disconnected.")
        if len(self._pending) >= 32:
            raise GadgetError("Too many pending requests.")
        request_id = str(uuid.uuid4())
        frame = self._cipher.request(request_id, method, params)
        future: asyncio.Future[Json] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            await self._socket.send(dumps(frame))
            return await asyncio.wait_for(future, self.timeout)
        except (TimeoutError, ConnectionClosed) as error:
            raise ConnectionLost(
                "No acknowledgement received. Reconcile before retrying a mutation."
            ) from error
        finally:
            self._pending.pop(request_id, None)
            if not future.done():
                future.cancel()
            elif not future.cancelled():
                future.exception()  # retrieve a simultaneous disconnect error if send failed

    async def _receive(self) -> None:
        assert self._socket is not None and self._cipher is not None
        try:
            while True:
                frame = object_value(loads(await self._socket.recv()))
                if "encrypted" not in frame:
                    self._plaintext(frame)
                    continue
                payload = self._cipher.decode(frame)
                message_id = string(frame["messageId"])
                if message_id in self._seen:
                    continue
                self._seen[message_id] = None
                if len(self._seen) > 4096:
                    self._seen.popitem(last=False)
                if frame["type"] == "res":
                    future = self._pending.get(string(frame["id"]))
                    if future and not future.done():
                        response = object_value(payload)
                        if "error" in response:
                            future.set_exception(
                                self._remote_error(object_value(response["error"]))
                            )
                        elif "result" in response:
                            future.set_result(response["result"])
                        else:
                            raise ProtocolError("Response has no result or error.")
                    continue
                event = object_value(payload)
                if event.get("type") == "ready":
                    proof = object_value(event.get("desktopProof"))
                    if proof.get("nonce") != self._hello_nonce or not self._hello_nonce:
                        raise ProtocolError("Desktop handshake nonce mismatch.")
                    verify(
                        string(self._state["desktopPublicKey"]),
                        proof.get("signature"),
                        "\n".join(
                            [
                                "moldable-remote-desktop-proof-v1",
                                self._hello_nonce,
                                string(self._state["desktopId"]),
                                "2",
                            ]
                        ),
                    )
                    self._hello_nonce = ""
                    self.ready = event
                    self._ready_event.set()
                    continue
                sequence = frame.get("seq")
                if type(sequence) is int:
                    if self._sequence is not None and sequence != self._sequence + 1:
                        raise ReplayRequired(
                            "Relay event gap. Reconnect and replay from your cursor."
                        )
                    self._sequence = sequence
                if self._collect_events:
                    try:
                        self._events.put_nowait(event)
                    except asyncio.QueueFull as error:
                        raise ReplayRequired(
                            "Event queue overflow. Reconnect and replay from your cursor."
                        ) from error
        except ConnectionClosed as error:
            code = error.rcvd.code if error.rcvd else None
            if code == 4003:
                self.store.forget()
                self._fail(PairingRequired("Device access was revoked. Pair again."))
            else:
                self._force_refresh = code == 4002
                self._fail(
                    ConnectionLost("Relay connection ended. Reconnect and reconcile delivery.")
                )
        except GadgetError as error:
            self._fail(error)
        except (ValueError, TypeError, KeyError, OSError):
            self._fail(ProtocolError("Invalid peer data or transport failure."))
        finally:
            await self._socket.close()

    def _plaintext(self, frame: Object) -> None:
        if frame.get("type") == "event" and frame.get("event") == "access.revoked":
            self.store.forget()
            raise PairingRequired("Device access was revoked. Pair again.")
        if frame.get("type") == "res" and "error" in frame:
            error = object_value(frame["error"])
            code = routing(error.get("code"))
            if code in ("session_expired", "authentication_expired", "invalid_session"):
                self._force_refresh = True
                raise ConnectionLost("Relay session expired. Reconnect to refresh.")
            future = self._pending.get(string(frame.get("id")))
            if future and not future.done():
                future.set_exception(RemoteError(code, "Relay rejected the request: " + code))
            return
        if frame.get("type") == "event" and frame.get("event") == "remote.event":
            params = object_value(frame.get("params"))
            if params.get("type") == "presence" and type(params.get("online")) is bool:
                return  # Relay presence is advisory, never authoritative application state.
        raise ProtocolError("Unexpected plaintext application frame.")

    @staticmethod
    def _remote_error(value: Object) -> RemoteError:
        return RemoteError(
            routing(value.get("code", "remote_error")),
            string(value.get("message", "Request rejected."), limit=4096),
        )

    def _fail(self, error: GadgetError) -> None:
        self._failure = error
        self._ready_event.set()
        for future in self._pending.values():
            if not future.done():
                future.set_exception(error)
        while not self._events.empty():
            self._events.get_nowait()
        self._events.put_nowait(error)

    async def events(self) -> AsyncIterator[Object]:
        """One consumer; events from subscription onward. Filter by workspace/channel."""
        if self._collect_events:
            raise GadgetError("Only one live event consumer is supported.")
        self._collect_events = True
        try:
            while True:
                if self._failure:
                    raise self._failure
                item = await self._events.get()
                if isinstance(item, GadgetError):
                    raise item
                yield item
        finally:
            self._collect_events = False
            while not self._events.empty():
                self._events.get_nowait()

    async def select_workspace(self, workspace_id: str) -> None:
        """Select the host's Remote workspace; current hosts share it across controllers."""
        routing(workspace_id)
        workspaces = self.ready.get("workspaces")
        if not isinstance(workspaces, list) or not any(
            isinstance(item, dict) and item.get("id") == workspace_id for item in workspaces
        ):
            raise GadgetError("Workspace was not advertised by the paired desktop.")
        result = object_value(
            await self._request("workspace.select", {"workspaceID": workspace_id})
        )
        if result.get("ok") is not True:
            raise ProtocolError("Workspace selection was not acknowledged.")
        self._workspace = workspace_id

    async def bots(self, workspace_id: str) -> Json:
        self._require_workspace(workspace_id)
        return await self._request("bots.list", {"workspaceId": routing(workspace_id)})

    async def transcript(
        self, workspace_id: str, bot_id: str, *, before_sequence: int | None = None, limit: int = 50
    ) -> Object:
        params = self._scope(workspace_id, bot_id)
        params["limit"] = self._limit(limit)
        if before_sequence is not None:
            if type(before_sequence) is not int or before_sequence < 0:
                raise ValueError("before_sequence must be a non-negative integer")
            params["beforeSequence"] = before_sequence
        return object_value(await self._request("bot.transcript", params))

    async def replay(
        self, workspace_id: str, bot_id: str, *, after_cursor: str | None = None, limit: int = 50
    ) -> Object:
        params = self._scope(workspace_id, bot_id)
        params["limit"] = self._limit(limit)
        if after_cursor is not None:
            params["afterCursor"] = string(after_cursor, limit=4096)
        return object_value(await self._request("bot.events.replay", params))

    async def send_text(
        self,
        workspace_id: str,
        bot_id: str,
        text: str,
        *,
        mutation_id: str,
        thread_id: str | None = None,
    ) -> Json:
        params = self._scope(workspace_id, bot_id)
        if not isinstance(text, str) or not text.strip() or len(text.encode()) > 24_000:
            raise ValueError("text must contain 1–24000 UTF-8 bytes")
        params.update(
            {"body": {"text": text, "format": "plain"}, "clientMutationId": routing(mutation_id)}
        )
        if thread_id is not None:
            params["threadId"] = routing(thread_id)
        return await self._request("bot.message.append", params)

    async def interrupt(
        self, workspace_id: str, bot_id: str, turn_id: str, *, thread_id: str
    ) -> Json:
        params = self._scope(workspace_id, bot_id)
        params["turnId"] = routing(turn_id)
        params["threadId"] = routing(thread_id)
        return await self._request("bot.turn.interrupt", params)

    def _require_workspace(self, workspace_id: str) -> None:
        if workspace_id != self._workspace:
            raise GadgetError("Call select_workspace with this workspace before making requests.")

    def _scope(self, workspace_id: str, bot_id: str) -> Object:
        self._require_workspace(workspace_id)
        return {"workspaceId": routing(workspace_id), "botId": routing(bot_id)}

    @staticmethod
    def _limit(limit: int) -> int:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("limit must be an integer from 1 to 100")
        return limit
