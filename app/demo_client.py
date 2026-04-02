from __future__ import annotations

import argparse
import asyncio
from datetime import datetime

from pydantic import BaseModel, Field

from app.sdk.client import ConfigClient


class TimeoutConfig(BaseModel):
    timeout_ms: int = Field(ge=1)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Example microservice that hot-reloads config over websocket.")
    parser.add_argument("--base-url", default="http://localhost:8080")
    parser.add_argument("--name", default="checkout-service.timeout")
    parser.add_argument("--target", default="checkout-service")
    parser.add_argument("--client-id", default="demo-client-a")
    parser.add_argument("--ttl", type=int, default=30)
    parser.add_argument("--app-version", default="demo-client")
    parser.add_argument("--simulate-failure-every", type=int, default=0)
    return parser.parse_args()


async def run() -> None:
    args = parse_args()
    client = ConfigClient[TimeoutConfig](
        base_url=args.base_url,
        client_id=args.client_id,
        target=args.target,
        ttl_seconds=args.ttl,
    )
    try:
        current = client.get_typed(args.name, TimeoutConfig)
        print(f"[{datetime.now().isoformat()}] boot config -> timeout_ms={current.timeout_ms}")

        async def on_update(config: TimeoutConfig, event: dict) -> None:
            print(
                f"[{datetime.now().isoformat()}] {event['event']} "
                f"version={event['version']} stable={event['stable_version']} "
                f"timeout_ms={config.timeout_ms}"
            )

        async def worker() -> None:
            handled_requests = 0
            while True:
                config = client.get_typed(args.name, TimeoutConfig)
                handled_requests += 1
                if args.simulate_failure_every and handled_requests % args.simulate_failure_every == 0:
                    raise RuntimeError("simulated request-path failure")
                print(f"[{datetime.now().isoformat()}] request handled with timeout_ms={config.timeout_ms}")
                await asyncio.sleep(3)

        await asyncio.gather(worker(), client.watch(args.name, TimeoutConfig, on_update))
    except Exception as exc:
        client.report_failure(
            args.name,
            exc,
            source="demo-client",
            app_version=args.app_version,
            metadata={"simulate_failure_every": args.simulate_failure_every or None},
        )
        raise
    finally:
        client.close()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
