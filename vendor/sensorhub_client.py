"""sensorhub_client: a single-file client for the SensorHub bus.

Copy this file into any project. It needs only two packages and nothing from SensorHub:

    pip install eclipse-zenoh msgpack

Quick start:

    from sensorhub_client import Hub

    hub = Hub()                                   # SENSORHUB_ROUTER=tcp/<robot-ip>:7447
    msg = hub.get("hub/sensors/jetson/stats", timeout=3)
    print(msg.data["temps_c"]["cpu"])

    for msg in hub.subscribe("hub/sensors/battery/telemetry"):     # blocking iterator
        print(msg.data["soc_pct"])

    hub.watch("hub/sensors/**")                   # keep the latest message of every topic
    msg = hub.latest("hub/sensors/gps/fix")       # None until the first message arrives
    hub.latest_age("hub/sensors/gps/fix")         # seconds since it arrived

    hub.command("my_sensor", "reset")             # {"ok": True, ...}

Wire format (envelope version 1): every message is a msgpack map
{"v": 1, "src", "seq", "t_mono_ns", "t_wall_ns", "data"}. See docs/INTEGRATION.md.

Thread safety: a Hub can be used from several threads. Subscriber callbacks run on a Zenoh
thread, so keep them quick (or hand the message to a queue).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
import time
from collections import deque
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

import msgpack
import zenoh

__all__ = ["Hub", "Message", "Subscription", "CommandError", "DEFAULT_ROUTER"]
__version__ = "1.0.0"

ENVELOPE_VERSION = 1
DEFAULT_ROUTER = "tcp/127.0.0.1:7447"
ENV_ROUTER = "SENSORHUB_ROUTER"
NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")

log = logging.getLogger("sensorhub_client")


@dataclass(frozen=True)
class Message:
    """One decoded bus message.

    `t_mono_ns` is the publisher's monotonic clock: use it to align streams that come from
    the same machine, never across machines. `t_wall_ns` is the publisher's wall clock.
    """

    topic: str
    src: str
    seq: int
    t_mono_ns: int
    t_wall_ns: int
    data: Any


class CommandError(Exception):
    """Raised by Hub.command(..., raise_on_error=True) when the driver refuses a command."""


def could_match_video(pattern: str) -> bool:
    """Can this key expression match anything under hub/video/? (Conservative.)"""
    chunks = pattern.split("/")
    for i, want in enumerate(("hub", "video")):
        if i >= len(chunks):
            return False
        head = chunks[i]
        if head == "**":
            return True
        if head != "*" and head != want:
            if "*" not in head:
                return False
            if not re.fullmatch(".*".join(re.escape(p) for p in head.split("*")), want):
                return False
    return True


def _decode(topic: str, payload: bytes) -> Message:
    """Decode an envelope. Raises ValueError for anything malformed or from another version."""
    try:
        obj = msgpack.unpackb(payload, raw=False)
    except Exception as exc:
        raise ValueError("not msgpack: %s" % exc) from exc
    if not isinstance(obj, dict):
        raise ValueError("envelope is not a map")
    if obj.get("v") != ENVELOPE_VERSION:
        raise ValueError("unsupported envelope version %r" % (obj.get("v"),))
    try:
        return Message(topic, obj["src"], obj["seq"], obj["t_mono_ns"], obj["t_wall_ns"],
                       obj["data"])
    except KeyError as exc:
        raise ValueError("envelope missing field %s" % exc) from exc


def _encode(src: str, seq: int, data: Any) -> bytes:
    return msgpack.packb(
        {"v": ENVELOPE_VERSION, "src": src, "seq": seq, "t_mono_ns": time.monotonic_ns(),
         "t_wall_ns": time.time_ns(), "data": data},
        use_bin_type=True,
    )


class Subscription:
    """A live subscription. Iterate it, call next(), or pass a callback to Hub.subscribe().

    In iterator mode messages wait in a bounded queue; when it is full the OLDEST message is
    dropped (`dropped` counts them), so a slow reader never blocks the robot.
    """

    def __init__(self, hub: Hub, topic: str, callback: Callable[[Message], None] | None,
                 queue_size: int):
        self.topic = topic
        self.dropped = 0
        self._callback = callback
        self._queue: deque[Message] = deque(maxlen=queue_size)
        self._cond = threading.Condition()
        self._closed = False
        self._sub = hub._session.declare_subscriber(topic, self._on_sample)

    def _on_sample(self, sample: Any) -> None:
        try:
            msg = _decode(str(sample.key_expr), bytes(sample.payload))
        except ValueError as exc:
            log.warning("skipping malformed message on %s: %s", sample.key_expr, exc)
            return
        if self._callback is not None:
            try:
                self._callback(msg)
            except Exception:
                log.exception("subscriber callback failed for %s", self.topic)
            return
        with self._cond:
            if len(self._queue) == self._queue.maxlen:
                self.dropped += 1
            self._queue.append(msg)
            self._cond.notify()

    def next(self, timeout: float | None = None) -> Message:
        """Next message. Raises TimeoutError, or StopIteration once closed and drained."""
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._cond:
            while not self._queue:
                if self._closed:
                    raise StopIteration
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    raise TimeoutError("no message on %s within %gs" % (self.topic, timeout))
                self._cond.wait(remaining)
            return self._queue.popleft()

    def __iter__(self) -> Iterator[Message]:
        while True:
            try:
                yield self.next()
            except StopIteration:
                return

    async def aiter(self):
        """Async iterator for asyncio code: `async for msg in sub.aiter(): ...`."""
        loop = asyncio.get_running_loop()
        while True:
            status, msg = await loop.run_in_executor(None, self._poll, 0.5)
            if status == "closed":
                return
            if status == "message":
                yield msg

    def _poll(self, timeout: float) -> tuple[str, Message | None]:
        try:
            return "message", self.next(timeout)
        except TimeoutError:
            return "timeout", None
        except StopIteration:
            return "closed", None

    def close(self) -> None:
        with self._cond:
            if self._closed:
                return
            self._closed = True
            self._cond.notify_all()
        try:
            self._sub.undeclare()
        except Exception:
            pass

    def __enter__(self) -> Subscription:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


class Hub:
    """A connection to the SensorHub router.

    router: "tcp/<host>:7447"; default from $SENSORHUB_ROUTER, else tcp/127.0.0.1:7447.
    connect_timeout: seconds to keep retrying (None = forever); then ConnectionError.
    src: the `src` written into messages you publish (default "client_<pid>").
    """

    def __init__(self, router: str | None = None, connect_timeout: float | None = 10.0,
                 src: str | None = None):
        self.router = router or os.environ.get(ENV_ROUTER, DEFAULT_ROUTER)
        self.src = src or "client_%d" % os.getpid()
        self._lock = threading.Lock()
        self._pubs: dict[str, Any] = {}
        self._seq: dict[str, int] = {}
        self._subs: list[Subscription] = []
        self._latest: dict[str, tuple[Message, float]] = {}
        self._latest_lock = threading.Lock()
        self._closed = False
        self._session = self._connect(connect_timeout)

    # ---- connection ----
    def _connect(self, timeout: float | None) -> Any:
        config = zenoh.Config()
        config.insert_json5("mode", '"client"')
        config.insert_json5("connect/endpoints", json.dumps([self.router]))
        config.insert_json5("scouting/multicast/enabled", "false")
        start, delay, last = time.monotonic(), 0.2, None
        while True:
            try:
                return zenoh.open(config)
            except Exception as exc:  # router not up yet
                last = exc
            if timeout is not None and time.monotonic() - start >= timeout:
                raise ConnectionError(
                    "could not connect to the SensorHub router at %s after %gs (%s). Is it "
                    "running and reachable? Set %s to change the address."
                    % (self.router, timeout, last, ENV_ROUTER)) from last
            time.sleep(delay)
            delay = min(delay * 2, 5.0)

    def is_connected(self) -> bool:
        """True while a router session exists. Zenoh reconnects by itself after a drop."""
        try:
            if self._session.is_closed():
                return False
            return any(True for _ in self._session.info.routers_zid())
        except Exception:
            return False

    # ---- subscribing ----
    def subscribe(self, topic: str, callback: Callable[[Message], None] | None = None,
                  queue_size: int = 256, allow_video: bool = False) -> Subscription:
        """Subscribe to a topic or wildcard ("hub/sensors/**").

        Without a callback, iterate the result. Patterns that could match camera video are
        refused unless allow_video=True, because a video subscriber switches the cameras on.
        """
        if could_match_video(topic) and not allow_video:
            raise ValueError("%r could match hub/video/**, which would start every camera; "
                             "pass allow_video=True if you really want that" % topic)
        sub = Subscription(self, topic, callback, queue_size)
        with self._lock:
            self._subs.append(sub)
        return sub

    def get(self, topic: str, timeout: float = 5.0) -> Message:
        """Wait for the next message on topic. Raises TimeoutError."""
        with self.subscribe(topic, queue_size=1) as sub:
            return sub.next(timeout)

    # ---- latest-value cache (for web backends and polling loops) ----
    def watch(self, *topics: str) -> None:
        """Start keeping the most recent message of every topic matching these patterns.

        Call once at startup; then read with latest(). Safe to call again for more topics.
        """
        for topic in topics:
            self.subscribe(topic, callback=self._remember)

    def _remember(self, msg: Message) -> None:
        with self._latest_lock:
            self._latest[msg.topic] = (msg, time.monotonic())

    def latest(self, topic: str) -> Message | None:
        """Most recent message on this exact topic since watch(), or None."""
        with self._latest_lock:
            entry = self._latest.get(topic)
        return entry[0] if entry else None

    def latest_age(self, topic: str) -> float | None:
        """Seconds since the latest message ARRIVED here (clock-offset free), or None."""
        with self._latest_lock:
            entry = self._latest.get(topic)
        return None if entry is None else time.monotonic() - entry[1]

    def fresh(self, topic: str, max_age_s: float) -> Message | None:
        """The latest message if it arrived within max_age_s seconds, else None."""
        age = self.latest_age(topic)
        return self.latest(topic) if age is not None and age <= max_age_s else None

    def snapshot(self) -> dict[str, tuple[Message, float]]:
        """{topic: (message, age_s)} for everything seen since watch()."""
        now = time.monotonic()
        with self._latest_lock:
            return {t: (m, now - at) for t, (m, at) in self._latest.items()}

    # ---- publishing and commands ----
    def publish(self, topic: str, data: Any) -> int:
        """Publish any msgpack-able value in the standard envelope. Returns its seq.

        Never blocks the sender: with a congested link, old messages are dropped.
        """
        with self._lock:
            pub = self._pubs.get(topic)
            if pub is None:
                pub = self._pubs[topic] = self._session.declare_publisher(
                    topic, congestion_control=zenoh.CongestionControl.DROP)
                self._seq[topic] = 0
            seq = self._seq[topic]
            self._seq[topic] = seq + 1
            pub.put(_encode(self.src, seq, data))
        return seq

    def command(self, instance: str, name: str, data: Any = None, timeout: float = 5.0,
                raise_on_error: bool = False) -> dict:
        """Send a command to a driver and wait for its answer.

        Returns {"cmd", "cmd_src", "cmd_seq", "ok", "error"}. Raises TimeoutError if nothing
        answers (driver down, or it takes no commands). With raise_on_error=True a refused
        command raises CommandError instead of returning ok=False.
        """
        for part in (instance, name):
            if not NAME_RE.match(part):
                raise ValueError("invalid name %r (expected %s)" % (part, NAME_RE.pattern))
        result_topic = "hub/sensors/%s/cmd_result" % instance
        with self.subscribe(result_topic) as sub:
            seq = self.publish("hub/cmd/%s/%s" % (instance, name), {} if data is None else data)
            deadline = time.monotonic() + timeout
            while True:
                try:
                    msg = sub.next(max(deadline - time.monotonic(), 0.001))
                except TimeoutError:
                    raise TimeoutError(
                        "no result from %r for command %r within %gs: is the driver running, "
                        "and does it accept commands?" % (instance, name, timeout)) from None
                r = msg.data
                if (isinstance(r, dict) and r.get("cmd_src") == self.src
                        and r.get("cmd_seq") == seq and r.get("cmd") == name):
                    if raise_on_error and not r.get("ok"):
                        raise CommandError(r.get("error") or "command refused")
                    return r

    # ---- driver health ----
    def driver_status(self, instance: str, timeout: float = 2.5) -> dict | None:
        """The driver's latest heartbeat map, or None if none arrives (driver down).

        Keys: state (starting|running|degraded|error), driver, pid, started_wall_ns,
        published, last_error. A heartbeat comes every second; treat >5 s as stale.
        """
        if not NAME_RE.match(instance):
            raise ValueError("invalid instance name %r" % (instance,))
        try:
            return self.get("hub/meta/drivers/%s/status" % instance, timeout).data
        except TimeoutError:
            return None

    # ---- lifecycle ----
    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for sub in list(self._subs):
            sub.close()
        for pub in list(self._pubs.values()):
            try:
                pub.undeclare()
            except Exception:
                pass
        try:
            self._session.close()
        except Exception:
            pass

    def __enter__(self) -> Hub:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()
