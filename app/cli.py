from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import httpx


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Config control plane CLI")
    parser.add_argument("--base-url", default="http://localhost:8080")
    parser.add_argument("--user", default="cli-operator")
    parser.add_argument("--role", default="operator")
    subparsers = parser.add_subparsers(dest="command", required=True)

    push = subparsers.add_parser("push", help="Create a new immutable config version")
    push.add_argument("--name", required=True)
    push.add_argument("--value-file", required=True, type=Path)
    push.add_argument("--schema-file", type=Path)
    push.add_argument("--description")

    get = subparsers.add_parser("get", help="Fetch config")
    get.add_argument("--name", required=True)
    get.add_argument("--version", default="resolved")
    get.add_argument("--target")
    get.add_argument("--client-id")

    versions = subparsers.add_parser("versions", help="List version history")
    versions.add_argument("--name", required=True)

    rollout = subparsers.add_parser("rollout", help="Start canary or 100% rollout")
    rollout.add_argument("--name", required=True)
    rollout.add_argument("--target", required=True)
    rollout.add_argument("--percent", required=True, type=int)
    rollout.add_argument("--metric")
    rollout.add_argument("--threshold", type=float)
    rollout.add_argument("--window", type=int, default=5)

    rollback = subparsers.add_parser("rollback", help="Rollback to a prior version")
    rollback.add_argument("--name", required=True)
    rollback.add_argument("--target-version", required=True, type=int)
    rollback.add_argument("--target")

    promote = subparsers.add_parser("promote", help="Promote an active partial rollout to 100% stable")
    promote.add_argument("--name", required=True)
    promote.add_argument("--rollout-id", required=True)

    audit = subparsers.add_parser("audit", help="Read audit log")
    audit.add_argument("--name")

    failures = subparsers.add_parser("failures", help="List recent anonymous client failure reports")
    failures.add_argument("--name")
    failures.add_argument("--target")
    failures.add_argument("--source")
    failures.add_argument("--limit", type=int, default=50)

    failure_summary = subparsers.add_parser("failure-summary", help="Aggregate anonymous client failure reports")
    failure_summary.add_argument("--name")
    failure_summary.add_argument("--target")
    failure_summary.add_argument("--window-minutes", type=int, default=60)
    failure_summary.add_argument("--limit", type=int, default=20)

    dry = subparsers.add_parser("dry-run-migration", help="Validate a new schema against existing versions")
    dry.add_argument("--name", required=True)
    dry.add_argument("--schema-file", required=True, type=Path)
    dry.add_argument("--value-file", type=Path)

    metric = subparsers.add_parser("simulate-metric", help="Set a synthetic canary metric value")
    metric.add_argument("--target", required=True)
    metric.add_argument("--metric", required=True)
    metric.add_argument("--value", required=True, type=float)

    subparsers.add_parser("list", help="List configs")
    return parser.parse_args()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    args = parse_args()
    headers = {"X-User-Id": args.user, "X-Role": args.role}
    with httpx.Client(base_url=args.base_url.rstrip("/"), headers=headers, timeout=10.0) as client:
        if args.command == "push":
            payload = {
                "name": args.name,
                "value": load_json(args.value_file),
                "schema": load_json(args.schema_file) if args.schema_file else None,
                "description": args.description,
            }
            response = client.post("/configs", json=payload)
        elif args.command == "get":
            response = client.get(
                f"/configs/{args.name}",
                params={"version": args.version, "target": args.target, "client_id": args.client_id},
            )
        elif args.command == "versions":
            response = client.get(f"/configs/{args.name}/versions")
        elif args.command == "rollout":
            canary = None
            if args.metric and args.threshold is not None:
                canary = {"metric": args.metric, "threshold": args.threshold, "window": args.window}
            response = client.post(
                f"/configs/{args.name}/rollout",
                json={"target": args.target, "percent": args.percent, "canary_check": canary},
            )
        elif args.command == "rollback":
            response = client.post(
                f"/configs/{args.name}/rollback",
                json={"target_version": args.target_version, "target": args.target},
            )
        elif args.command == "promote":
            response = client.post(f"/configs/{args.name}/rollouts/{args.rollout_id}/promote")
        elif args.command == "audit":
            response = client.get("/audit", params={"name": args.name})
        elif args.command == "failures":
            response = client.get(
                "/telemetry/failures",
                params={
                    "config_name": args.name,
                    "target": args.target,
                    "source": args.source,
                    "limit": args.limit,
                },
            )
        elif args.command == "failure-summary":
            response = client.get(
                "/telemetry/failures/summary",
                params={
                    "config_name": args.name,
                    "target": args.target,
                    "window_minutes": args.window_minutes,
                    "limit": args.limit,
                },
            )
        elif args.command == "dry-run-migration":
            response = client.post(
                f"/configs/{args.name}/schema/dry-run",
                json={
                    "schema": load_json(args.schema_file),
                    "value": load_json(args.value_file) if args.value_file else None,
                },
            )
        elif args.command == "simulate-metric":
            response = client.post(
                "/simulation/metrics",
                json={"target": args.target, "metric": args.metric, "value": args.value},
            )
        else:
            response = client.get("/configs")

    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        print(json.dumps({"status": response.status_code, "error": exc.response.text}, indent=2))
        raise SystemExit(1) from exc
    print(json.dumps(response.json() if response.content else None, indent=2))


if __name__ == "__main__":
    main()
