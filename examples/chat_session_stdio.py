#!/usr/bin/env python3
"""Run a small multi-turn Codex app-server chat over stdio transport.

This example is intentionally a bit richer than "hello world":
- explicit initialize handshake
- multi-turn chat reusing one thread id
- metadata on each turn
- command override for launching app-server
- robust exception handling and clean shutdown
"""

from __future__ import annotations

import argparse
import asyncio
import shlex
import sys

from codex_app_server_client import (
    CodexClient,
    CodexProtocolError,
    CodexTimeoutError,
    CodexTransportError,
)

DEFAULT_PROMPTS = [
    "I am testing a Python API client for codex app-server.",
    "Give me a short checklist for robust websocket/stdio client design.",
    "Now summarize that checklist into 3 bullets.",
]


def parse_args() -> argparse.Namespace:
    """Parse CLI options for the stdio example."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--prompt",
        action="append",
        dest="prompts",
        help="Prompt to send. Can be provided multiple times.",
    )
    parser.add_argument(
        "--user",
        default="example-stdio-user",
        help="Optional user id forwarded in turn payload.",
    )
    parser.add_argument(
        "--cmd",
        help="Command used to launch app-server, e.g. 'codex app-server --port 0'.",
    )
    parser.add_argument(
        "--turn-timeout",
        type=float,
        default=180.0,
        help="Timeout in seconds for each chat turn.",
    )
    return parser.parse_args()


async def run_session(args: argparse.Namespace) -> int:
    """Run multi-turn chat session and print structured output."""
    prompts = args.prompts or DEFAULT_PROMPTS
    command = shlex.split(args.cmd) if args.cmd else None

    client = await CodexClient.connect_stdio(
        command=command,
        turn_timeout=args.turn_timeout,
    )

    thread_id: str | None = None
    try:
        init = await client.initialize()
        protocol = init.protocol_version or "unknown"
        print(f"[init] protocol_version={protocol}")

        for index, prompt in enumerate(prompts, start=1):
            print(f"\n[user:{index}] {prompt}")
            result = await client.chat_once(
                prompt,
                thread_id=thread_id,
                user=args.user,
                metadata={
                    "example": "stdio",
                    "turn_index": index,
                    "client": "codex-app-server-client",
                },
            )
            thread_id = result.thread_id
            print(f"[assistant:{index}] {result.final_text}")
            print(
                "[meta]"
                f" thread_id={result.thread_id}"
                f" turn_id={result.turn_id}"
                f" events={len(result.raw_events)}"
            )
        return 0
    except CodexTimeoutError as exc:
        print(f"[error] timeout: {exc}", file=sys.stderr)
        return 2
    except CodexProtocolError as exc:
        details = f" code={exc.code}" if exc.code is not None else ""
        print(f"[error] protocol:{details} {exc}", file=sys.stderr)
        return 3
    except CodexTransportError as exc:
        print(f"[error] transport: {exc}", file=sys.stderr)
        return 4
    except KeyboardInterrupt:
        print("\n[interrupt] user cancelled session", file=sys.stderr)
        return 130
    finally:
        await client.close()


def main() -> None:
    """CLI entrypoint."""
    args = parse_args()
    raise SystemExit(asyncio.run(run_session(args)))


if __name__ == "__main__":
    main()
