from __future__ import annotations

import pytest
from fastapi import HTTPException, WebSocketException

from app.core.security import Actor, actor_from_identity, get_websocket_actor, require_role


class FakeWebSocket:
    def __init__(self, *, headers: dict[str, str] | None = None, query_params: dict[str, str] | None = None) -> None:
        self.headers = headers or {}
        self.query_params = query_params or {}


def test_actor_from_identity_defaults_to_anonymous_reader():
    actor = actor_from_identity(None, None)

    assert actor.user_id == "anonymous"
    assert actor.role == "reader"


def test_actor_from_identity_preserves_explicit_values():
    actor = actor_from_identity("alice", "admin")

    assert actor.user_id == "alice"
    assert actor.role == "admin"


def test_actor_from_identity_rejects_invalid_role():
    with pytest.raises(HTTPException) as exc:
        actor_from_identity("alice", "owner")

    assert exc.value.status_code == 403


def test_get_websocket_actor_reads_headers():
    actor = get_websocket_actor(FakeWebSocket(headers={"x-user-id": "ws-user", "x-role": "operator"}))

    assert actor.user_id == "ws-user"
    assert actor.role == "operator"


def test_get_websocket_actor_falls_back_to_query_params():
    actor = get_websocket_actor(FakeWebSocket(query_params={"user_id": "query-user", "role": "admin"}))

    assert actor.user_id == "query-user"
    assert actor.role == "admin"


def test_get_websocket_actor_rejects_invalid_role():
    with pytest.raises(WebSocketException) as exc:
        get_websocket_actor(FakeWebSocket(headers={"x-role": "owner"}))

    assert exc.value.code == 1008


@pytest.mark.parametrize("role", ["admin", "operator"])
def test_require_role_allows_permitted_roles(role):
    dependency = require_role("admin", "operator")

    actor = dependency(Actor(user_id="allowed", role=role))

    assert actor.role == role


def test_require_role_rejects_forbidden_role():
    dependency = require_role("admin")

    with pytest.raises(HTTPException) as exc:
        dependency(Actor(user_id="reader", role="reader"))

    assert exc.value.status_code == 403
