#!/usr/bin/env python3
"""Run a small multi-turn Codex app-server chat over websocket transport.

This example demonstrates:
- websocket URL/token configuration
- explicit initialize handshake
- multi-turn chat in one thread
- inactivity timeout with continuation resume
- per-turn metadata and user fields
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

from codex_app_server_client import (
    ChatContinuation,
    CodexClient,
    CodexProtocolError,
    ChatResult,
    CodexTimeoutError,
    CodexTransportError,
    CodexTurnInactiveError,
)

DEFAULT_PROMPTS = [
    "I am connected over websocket. Reply with one sentence.",
    "Now give two practical tips for building resilient JSON-RPC clients.",
    "Finally, compress those tips into one line.",
]


def parse_args() -> argparse.Namespace:
    """Parse CLI options for the websocket example."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--url",
        default=os.getenv("CODEX_APP_SERVER_WS_URL", "ws://127.0.0.1:8765"),
        help="Websocket URL for app-server.",
    )
    parser.add_argument(
        "--token",
        default=os.getenv("CODEX_APP_SERVER_TOKEN"),
        help="Optional bearer token for websocket auth.",
    )
    parser.add_argument(
        "--prompt",
        action="append",
        dest="prompts",
        help="Prompt to send. Can be provided multiple times.",
    )
    parser.add_argument(
        "--user",
        default="example-websocket-user",
        help="Optional user id forwarded in turn payload.",
    )
    parser.add_argument(
        "--inactivity-timeout",
        type=float,
        default=120.0,
        help="Per-turn inactivity timeout in seconds (<=0 disables inactivity timeout).",
    )
    return parser.parse_args()


def _normalize_timeout(timeout: float) -> float | None:
    if timeout <= 0:
        return None
    return timeout


async def _chat_once_with_resume(
    client: CodexClient,
    *,
    prompt: str,
    thread_id: str | None,
    user: str,
    metadata: dict[str, object],
    inactivity_timeout: float | None,
) -> tuple[str, ChatResult]:
    continuation: ChatContinuation | None = None

    while True:
        try:
            if continuation is None:
                result = await client.chat_once(
                    prompt,
                    thread_id=thread_id,
                    user=user,
                    metadata=metadata,
                    inactivity_timeout=inactivity_timeout,
                )
            else:
                result = await client.chat_once(
                    continuation=continuation,
                    inactivity_timeout=inactivity_timeout,
                )
            return result.thread_id, result
        except CodexTurnInactiveError as exc:
            continuation = exc.continuation
            print(
                "[warn]"
                f" turn inactive for {exc.idle_seconds:.1f}s"
                f" (thread_id={continuation.thread_id} turn_id={continuation.turn_id}); resuming...",
                file=sys.stderr,
            )


async def run_session(args: argparse.Namespace) -> int:
    """Run multi-turn websocket chat session and print structured output."""
    prompts = args.prompts or DEFAULT_PROMPTS
    inactivity_timeout = _normalize_timeout(args.inactivity_timeout)

    client = await CodexClient.connect_websocket(
        url=args.url,
        token=args.token,
        inactivity_timeout=inactivity_timeout,
    )

    thread_id: str | None = None
    try:
        init = await client.initialize()
        protocol = init.protocol_version or "unknown"
        print(f"[init] protocol_version={protocol} url={args.url}")

        for index, prompt in enumerate(prompts, start=1):
            print(f"\n[user:{index}] {prompt}")
            thread_id, result_obj = await _chat_once_with_resume(
                client,
                prompt=prompt,
                thread_id=thread_id,
                user=args.user,
                metadata={
                    "example": "websocket",
                    "turn_index": index,
                    "client": "codex-app-server-client",
                },
                inactivity_timeout=inactivity_timeout,
            )
            result = result_obj
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
