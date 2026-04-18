from __future__ import annotations

import argparse
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from app import cli


class DummyResponse:
    def __init__(self, *, payload=None, status_code: int = 200, text: str = "") -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = text
        self.content = b"" if payload is None else b"1"

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("GET", "http://localhost")
            response = httpx.Response(self.status_code, text=self.text, request=request)
            raise httpx.HTTPStatusError("boom", request=request, response=response)

    def json(self):
        return self._payload


class DummyClient:
    def __init__(self, response: DummyResponse) -> None:
        self.response = response
        self.calls: list[tuple[str, str, dict]] = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def post(self, url: str, **kwargs):
        self.calls.append(("post", url, kwargs))
        return self.response

    def get(self, url: str, **kwargs):
        self.calls.append(("get", url, kwargs))
        return self.response


def test_parse_labels_accepts_key_value_pairs():
    labels = cli.parse_labels(["team=platform", "owner=config"])

    assert labels == {"team": "platform", "owner": "config"}


def test_parse_labels_rejects_invalid_format():
    with pytest.raises(SystemExit):
        cli.parse_labels(["not-a-label"])


def test_load_json_reads_payload(tmp_path):
    path = tmp_path / "payload.json"
    path.write_text(json.dumps({"timeout_ms": 2000}), encoding="utf-8")

    assert cli.load_json(path) == {"timeout_ms": 2000}


@pytest.mark.parametrize(
    ("argv", "command", "expected_fields"),
    [
        (["configctl", "list"], "list", {}),
        (["configctl", "get", "--name", "checkout.timeout"], "get", {"name": "checkout.timeout"}),
        (
            ["configctl", "rollout", "--name", "checkout.timeout", "--target", "checkout", "--percent", "10"],
            "rollout",
            {"target": "checkout", "percent": 10},
        ),
        (
            ["configctl", "advance", "--name", "checkout.timeout", "--rollout-id", "roll-1", "--percent", "100"],
            "advance",
            {"rollout_id": "roll-1", "percent": 100},
        ),
        (
            ["configctl", "rollback", "--name", "checkout.timeout", "--target-version", "1"],
            "rollback",
            {"target_version": 1},
        ),
        (
            ["configctl", "simulate-metric", "--target", "checkout", "--metric", "error_rate", "--value", "0.1"],
            "simulate-metric",
            {"target": "checkout", "metric": "error_rate"},
        ),
    ],
)
def test_parse_args_supports_commands(monkeypatch, argv, command, expected_fields):
    monkeypatch.setattr("sys.argv", argv)

    args = cli.parse_args()

    assert args.command == command
    for key, value in expected_fields.items():
        assert getattr(args, key) == value


def test_main_routes_advance_command(monkeypatch, capsys):
    args = argparse.Namespace(
        base_url="http://localhost:8080",
        user="operator",
        role="operator",
        environment="prod",
        command="advance",
        name="checkout.timeout",
        rollout_id="roll-1",
        percent=10,
    )
    response = DummyResponse(payload={"status": "ok"})
    client = DummyClient(response)

    monkeypatch.setattr(cli, "parse_args", lambda: args)
    monkeypatch.setattr(cli.httpx, "Client", lambda **kwargs: client)

    cli.main()

    captured = capsys.readouterr()
    assert "\"status\": \"ok\"" in captured.out
    assert client.calls == [
        ("post", "/configs/checkout.timeout/rollouts/roll-1/advance", {"json": {"percent": 10}})
    ]


def test_main_prints_error_and_exits_on_http_failure(monkeypatch, capsys):
    args = argparse.Namespace(
        base_url="http://localhost:8080",
        user="reader",
        role="reader",
        environment="prod",
        command="list",
    )
    response = DummyResponse(status_code=403, text="forbidden")
    client = DummyClient(response)

    monkeypatch.setattr(cli, "parse_args", lambda: args)
    monkeypatch.setattr(cli.httpx, "Client", lambda **kwargs: client)

    with pytest.raises(SystemExit):
        cli.main()

    captured = capsys.readouterr()
    assert "\"status\": 403" in captured.out

