#!/usr/bin/env python3
"""Run a richer step-stream chat example with block output.

This example demonstrates:
- async step streaming via `client.chat(...)`
- non-delta completed step handling
- continuation resume on inactivity timeout
- optional turn cancellation with unread-step drain
- readable step blocks (`thinking`, `exec`, `codex`, etc.)
- both stdio and websocket transports
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shlex
import sys

from codex_app_server_client import (
    ChatContinuation,
    CodexClient,
    CodexProtocolError,
    CodexTimeoutError,
    CodexTransportError,
    CodexTurnInactiveError,
    ConversationStep,
)

DEFAULT_PROMPTS = [
    "Inspect what changed and explain your approach briefly.",
    "Run a suitable check command and summarize the result.",
    "Draft a concise commit message.",
]


def parse_args() -> argparse.Namespace:
    """Parse CLI options for the rich step-stream example."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--transport",
        choices=["stdio", "websocket"],
        default="stdio",
        help="Transport mode to use.",
    )
    parser.add_argument(
        "--url",
        default=os.getenv("CODEX_APP_SERVER_WS_URL", "ws://127.0.0.1:8765"),
        help="Websocket URL for app-server (used when --transport websocket).",
    )
    parser.add_argument(
        "--token",
        default=os.getenv("CODEX_APP_SERVER_TOKEN"),
        help="Optional bearer token (used when --transport websocket).",
    )
    parser.add_argument(
        "--cmd",
        help="Command used to launch app-server in stdio mode, e.g. 'codex app-server'.",
    )
    parser.add_argument(
        "--prompt",
        action="append",
        dest="prompts",
        help="Prompt to send. Can be provided multiple times.",
    )
    parser.add_argument(
        "--user",
        default="example-step-stream-user",
        help="Optional user id forwarded in turn payload.",
    )
    parser.add_argument(
        "--inactivity-timeout",
        type=float,
        default=120.0,
        help="Per-turn inactivity timeout in seconds (<=0 disables inactivity timeout).",
    )
    parser.add_argument(
        "--cancel-on-timeout",
        action="store_true",
        help="Cancel timed-out turns instead of auto-resuming them.",
    )
    parser.add_argument(
        "--show-data",
        action="store_true",
        help="Show compact JSON summary of each step payload.",
    )
    return parser.parse_args()


def _normalize_timeout(timeout: float) -> float | None:
    if timeout <= 0:
        return None
    return timeout


async def _connect_client(args: argparse.Namespace) -> CodexClient:
    """Create and connect a client for the selected transport."""
    inactivity_timeout = _normalize_timeout(args.inactivity_timeout)
    if args.transport == "stdio":
        command = shlex.split(args.cmd) if args.cmd else None
        return await CodexClient.connect_stdio(
            command=command,
            inactivity_timeout=inactivity_timeout,
        )

    return await CodexClient.connect_websocket(
        url=args.url,
        token=args.token,
        inactivity_timeout=inactivity_timeout,
    )


def _compact_data(step: ConversationStep) -> dict[str, object] | None:
    """Return a compact payload summary for optional display."""
    item = step.data.get("item") if isinstance(step.data, dict) else None
    if not isinstance(item, dict):
        return None

    if step.item_type == "commandExecution":
        return {
            "command": item.get("command"),
            "cwd": item.get("cwd"),
            "status": item.get("status"),
            "exitCode": item.get("exitCode"),
            "durationMs": item.get("durationMs"),
        }

    if step.item_type == "mcpToolCall":
        return {
            "server": item.get("server"),
            "tool": item.get("tool"),
            "status": item.get("status"),
            "error": item.get("error"),
        }

    if step.item_type == "fileChange":
        return {
            "status": item.get("status"),
            "changes_count": len(item.get("changes", []))
            if isinstance(item.get("changes"), list)
            else None,
        }

    return {"item_type": step.item_type, "item_id": step.item_id}


def _print_step(step: ConversationStep, *, show_data: bool) -> None:
    """Render one completed conversation step as a readable block."""
    line = "=" * 88
    print(line)
    print(
        f"[{step.step_type}]"
        f" item_type={step.item_type or '-'}"
        f" item_id={step.item_id or '-'}"
    )
    print(f"thread={step.thread_id} turn={step.turn_id}")
    print("-" * 88)

    text = step.text or "(no text)"
    print(text)

    if show_data:
        compact = _compact_data(step)
        if compact is not None:
            print("-" * 88)
            print("data:")
            print(json.dumps(compact, indent=2, sort_keys=True))

    print(line)


async def run_session(args: argparse.Namespace) -> int:
    """Run multi-turn step-streaming chat and print rich blocks."""
    prompts = args.prompts or DEFAULT_PROMPTS
    inactivity_timeout = _normalize_timeout(args.inactivity_timeout)
    client = await _connect_client(args)
    thread_id: str | None = None

    try:
        init = await client.initialize()
        protocol = init.protocol_version or "unknown"
        if args.transport == "websocket":
            print(
                f"[init] protocol_version={protocol}"
                f" transport=websocket url={args.url}"
            )
        else:
            print(f"[init] protocol_version={protocol} transport=stdio")

        for index, prompt in enumerate(prompts, start=1):
            print(f"\n[user:{index}] {prompt}")
            step_count = 0
            continuation: ChatContinuation | None = None

            while True:
                try:
                    if continuation is None:
                        stream = client.chat(
                            prompt,
                            thread_id=thread_id,
                            user=args.user,
                            metadata={
                                "example": "steps-rich",
                                "turn_index": index,
                                "transport": args.transport,
                            },
                            inactivity_timeout=inactivity_timeout,
                        )
                    else:
                        stream = client.chat(
                            continuation=continuation,
                            inactivity_timeout=inactivity_timeout,
                        )

                    async for step in stream:
                        step_count += 1
                        thread_id = step.thread_id
                        _print_step(step, show_data=args.show_data)
                    break
                except CodexTurnInactiveError as exc:
                    continuation = exc.continuation
                    print(
                        "[warn]"
                        f" turn inactive for {exc.idle_seconds:.1f}s"
                        f" (thread_id={continuation.thread_id} turn_id={continuation.turn_id})",
                        file=sys.stderr,
                    )

                    if not args.cancel_on_timeout:
                        print("[warn] resuming same turn...", file=sys.stderr)
                        continue

                    cancel_result = await client.cancel(continuation)
                    for unread_step in cancel_result.steps:
                        step_count += 1
                        thread_id = unread_step.thread_id
                        _print_step(unread_step, show_data=args.show_data)
                    print(
                        "[meta]"
                        f" cancelled turn_id={cancel_result.turn_id}"
                        f" unread_steps={len(cancel_result.steps)}"
                        f" unread_events={len(cancel_result.raw_events)}",
                        file=sys.stderr,
                    )
                    break

            print(f"[meta] thread_id={thread_id or '-'} steps={step_count}")

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
