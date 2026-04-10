from __future__ import annotations

import asyncio

import pytest

from app.services.notifications import NotificationHub


class FakeWebSocket:
    def __init__(self, *, fail_on_send: bool = False) -> None:
        self.fail_on_send = fail_on_send
        self.accepted = False
        self.sent_messages: list[dict] = []
        self.send_count = 0

    async def accept(self) -> None:
        self.accepted = True

    async def send_json(self, payload: dict) -> None:
        self.send_count += 1
        if self.fail_on_send and self.send_count > 1:
            raise RuntimeError("websocket send failed")
        self.sent_messages.append(payload)


@pytest.mark.asyncio
async def test_register_accepts_websocket_and_sends_connected_event():
    hub = NotificationHub()
    websocket = FakeWebSocket()

    await hub.register(websocket, "checkout.timeout", "prod", "checkout")

    assert websocket.accepted is True
    assert websocket.sent_messages[0]["event"] == "connected"
    assert len(hub._subscriptions) == 1  # noqa: SLF001


@pytest.mark.asyncio
async def test_unregister_removes_subscription():
    hub = NotificationHub()
    websocket = FakeWebSocket()
    await hub.register(websocket, "checkout.timeout", "prod", "checkout")

    await hub.unregister(websocket)

    assert hub._subscriptions == {}  # noqa: SLF001


@pytest.mark.parametrize(
    ("event", "config_name", "environment", "target", "expected"),
    [
        ({"config_name": "checkout.timeout", "environment": "prod", "target": "checkout"}, None, None, None, True),
        ({"config_name": "checkout.timeout", "environment": "prod", "target": "checkout"}, "checkout.timeout", None, None, True),
        ({"config_name": "checkout.timeout", "environment": "prod", "target": "checkout"}, "payments.timeout", None, None, False),
        ({"config_name": "checkout.timeout", "environment": "prod", "target": "checkout"}, None, "prod", None, True),
        ({"config_name": "checkout.timeout", "environment": "prod", "target": "checkout"}, None, "staging", None, False),
        ({"config_name": "checkout.timeout", "environment": "prod", "target": "checkout"}, None, None, "checkout", True),
        ({"config_name": "checkout.timeout", "environment": "prod", "target": "checkout"}, None, None, "payments", False),
    ],
)
def test_matches_applies_scopes(event, config_name, environment, target, expected):
    assert NotificationHub._matches(event, config_name, environment, target) is expected  # noqa: SLF001


@pytest.mark.asyncio
async def test_publish_delivers_matching_event_to_subscriber():
    hub = NotificationHub()
    websocket = FakeWebSocket()
    await hub.register(websocket, "checkout.timeout", "prod", "checkout")

    event = await hub.publish(
        {
            "event": "rollout_started",
            "config_name": "checkout.timeout",
            "environment": "prod",
            "target": "checkout",
            "version": 2,
            "stable_version": 1,
        }
    )

    assert event["sequence"] == 1
    assert websocket.sent_messages[-1]["event"] == "rollout_started"


@pytest.mark.asyncio
async def test_publish_skips_non_matching_subscriber():
    hub = NotificationHub()
    websocket = FakeWebSocket()
    await hub.register(websocket, "checkout.timeout", "prod", "payments")

    await hub.publish(
        {
            "event": "rollout_started",
            "config_name": "checkout.timeout",
            "environment": "prod",
            "target": "checkout",
            "version": 2,
            "stable_version": 1,
        }
    )

    assert [message["event"] for message in websocket.sent_messages] == ["connected"]


@pytest.mark.asyncio
async def test_publish_unregisters_stale_websocket_after_send_failure():
    hub = NotificationHub()
    websocket = FakeWebSocket(fail_on_send=True)
    await hub.register(websocket, "checkout.timeout", "prod", "checkout")

    await hub.publish(
        {
            "event": "rollout_started",
            "config_name": "checkout.timeout",
            "environment": "prod",
            "target": "checkout",
            "version": 2,
            "stable_version": 1,
        }
    )

    assert hub._subscriptions == {}  # noqa: SLF001


@pytest.mark.asyncio
async def test_poll_returns_matching_event_after_publish():
    hub = NotificationHub()
    await hub.publish(
        {
            "event": "rollout_started",
            "config_name": "checkout.timeout",
            "environment": "prod",
            "target": "checkout",
            "version": 2,
            "stable_version": 1,
        }
    )

    event = await hub.poll(0, "checkout.timeout", "prod", "checkout", timeout=0.01)

    assert event is not None
    assert event["event"] == "rollout_started"


@pytest.mark.asyncio
async def test_poll_ignores_events_at_or_before_last_sequence():
    hub = NotificationHub()
    event = await hub.publish(
        {
            "event": "rollout_started",
            "config_name": "checkout.timeout",
            "environment": "prod",
            "target": "checkout",
            "version": 2,
            "stable_version": 1,
        }
    )

    result = await hub.poll(event["sequence"], "checkout.timeout", "prod", "checkout", timeout=0.01)

    assert result is None


@pytest.mark.asyncio
async def test_poll_filters_out_non_matching_environment():
    hub = NotificationHub()
    await hub.publish(
        {
            "event": "rollout_started",
            "config_name": "checkout.timeout",
            "environment": "staging",
            "target": "checkout",
            "version": 2,
            "stable_version": 1,
        }
    )

    result = await hub.poll(0, "checkout.timeout", "prod", "checkout", timeout=0.01)

    assert result is None


@pytest.mark.asyncio
async def test_poll_times_out_when_no_event_arrives():
    hub = NotificationHub()

    result = await hub.poll(0, "checkout.timeout", "prod", "checkout", timeout=0.01)

    assert result is None


@pytest.mark.asyncio
async def test_poll_waits_for_future_event_before_timeout():
    hub = NotificationHub()

    async def publish_later() -> None:
        await asyncio.sleep(0.01)
        await hub.publish(
            {
                "event": "rollout_started",
                "config_name": "checkout.timeout",
                "environment": "prod",
                "target": "checkout",
                "version": 2,
                "stable_version": 1,
            }
        )

    task = asyncio.create_task(publish_later())
    try:
        result = await hub.poll(0, "checkout.timeout", "prod", "checkout", timeout=0.05)
    finally:
        await task

    assert result is not None
    assert result["version"] == 2


def test_observe_delivery_latency_ignores_missing_timestamp():
    NotificationHub._observe_delivery_latency({"event": "rollout_started"}, "websocket", "sent")  # noqa: SLF001
