from typing import Literal

from fastapi import Depends, Header, HTTPException, WebSocket, WebSocketException, status
from pydantic import BaseModel


Role = Literal["admin", "operator", "reader"]


class Actor(BaseModel):
    user_id: str
    role: Role


def actor_from_identity(user_id: str | None, role: str | None) -> Actor:
    resolved_role = role or "reader"
    if resolved_role not in {"admin", "operator", "reader"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"role '{resolved_role}' cannot perform this action",
        )
    return Actor(user_id=user_id or "anonymous", role=resolved_role)


def get_actor(
    x_user_id: str = Header(default="anonymous", alias="X-User-Id"),
    x_role: Role = Header(default="reader", alias="X-Role"),
) -> Actor:
    return actor_from_identity(x_user_id, x_role)


def get_websocket_actor(websocket: WebSocket) -> Actor:
    try:
        return actor_from_identity(
            websocket.headers.get("x-user-id") or websocket.query_params.get("user_id"),
            websocket.headers.get("x-role") or websocket.query_params.get("role"),
        )
    except HTTPException as exc:
        raise WebSocketException(
            code=status.WS_1008_POLICY_VIOLATION,
            reason=str(exc.detail),
        ) from exc


def require_role(*allowed: Role):
    def dependency(actor: Actor = Depends(get_actor)) -> Actor:
        if actor.role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"role '{actor.role}' cannot perform this action",
            )
        return actor

    return dependency
