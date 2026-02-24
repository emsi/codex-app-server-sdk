#!/usr/bin/env python3
"""Advanced thread configuration and forking example for codex-app-server-client."""

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
    ThreadConfig,
    TurnOverrides,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cmd",
        default="codex app-server",
        help="Stdio app-server command.",
    )
    parser.add_argument(
        "--cwd",
        default=".",
        help="Thread working directory.",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Optional model id for the initial thread.",
    )
    parser.add_argument(
        "--base-instructions",
        default="Be concise and precise.",
        help="Thread base instructions.",
    )
    parser.add_argument(
        "--developer-instructions",
        default="Prefer actionable suggestions.",
        help="Thread developer instructions.",
    )
    parser.add_argument(
        "--prompt",
        default="Summarize what this repository appears to do.",
        help="Prompt for the original thread.",
    )
    parser.add_argument(
        "--fork-prompt",
        default="Now focus on test strategy and suggest 3 concrete test cases.",
        help="Prompt for the forked thread.",
    )
    return parser.parse_args()


async def run(args: argparse.Namespace) -> int:
    command = shlex.split(args.cmd)

    try:
        async with CodexClient.connect_stdio(command=command) as client:
            thread = await client.start_thread(
                ThreadConfig(
                    cwd=args.cwd,
                    base_instructions=args.base_instructions,
                    developer_instructions=args.developer_instructions,
                    model=args.model,
                )
            )

            first = await thread.chat_once(args.prompt)
            print(f"[thread:{thread.thread_id}] {first.final_text}")

            await thread.update_defaults(
                ThreadConfig(
                    developer_instructions="Prioritize safety and edge cases.",
                )
            )

            forked = await thread.fork(
                overrides=ThreadConfig(
                    developer_instructions="Focus only on testing concerns.",
                )
            )

            print(f"[fork] parent={thread.thread_id} forked={forked.thread_id}")
            async for step in forked.chat(
                args.fork_prompt,
                turn_overrides=TurnOverrides(effort="low"),
            ):
                text = (step.text or "").strip()
                print(f"[{step.step_type}] {text}")

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


def main() -> None:
    args = parse_args()
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
