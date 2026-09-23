"""Opt-in smoke test against the installed app-server, using only GPT-6 Luna."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from codex_app_server_sdk import (
    CodexClient,
    CodexTurnInactiveError,
    ThreadConfig,
    TurnOverrides,
)

LIVE_MODEL = "gpt-6-luna"
LIVE_TIMEOUT_SECONDS = 60.0
CANCEL_IDLE_SECONDS = 0.001
LIVE_OPT_IN = "CODEX_SDK_LIVE_TESTS"
EXPECTED_FIRST = "LUNA_A"
EXPECTED_SECOND = "LUNA_B"


@pytest.mark.skipif(os.getenv(LIVE_OPT_IN) != "1", reason=f"set {LIVE_OPT_IN}=1 to run")
def test_live_concurrent_turns_and_cancellation(tmp_path: Path) -> None:
    """Run two tiny concurrent turns, then interrupt a third ephemeral turn.

    :param tmp_path: Isolated working directory for the live threads.
    :return: None.
    """

    async def run() -> None:
        """Exercise the public SDK against one real stdio connection."""
        command = [
            "codex",
            "app-server",
            "-c",
            f'model="{LIVE_MODEL}"',
            "-c",
            'model_reasoning_effort="low"',
            "-c",
            "mcp_servers={}",
        ]
        config = ThreadConfig(
            model=LIVE_MODEL,
            cwd=str(tmp_path),
            ephemeral=True,
            sandbox="read-only",
            approval_policy="never",
        )
        overrides = TurnOverrides(model=LIVE_MODEL, effort="low")
        async with asyncio.timeout(LIVE_TIMEOUT_SECONDS):
            async with CodexClient.connect_stdio(command=command) as client:
                first = await client.start_thread(config)
                second = await client.start_thread(config)
                results = await asyncio.gather(
                    first.chat_once(
                        f"Do not use tools. Reply exactly {EXPECTED_FIRST}.",
                        turn_overrides=overrides,
                    ),
                    second.chat_once(
                        f"Do not use tools. Reply exactly {EXPECTED_SECOND}.",
                        turn_overrides=overrides,
                    ),
                )
                assert [result.final_text for result in results] == [
                    EXPECTED_FIRST,
                    EXPECTED_SECOND,
                ]
                assert results[0].thread_id != results[1].thread_id
                with pytest.raises(CodexTurnInactiveError) as idle:
                    await first.chat_once(
                        "Do not use tools. Reply exactly LUNA_CANCEL.",
                        turn_overrides=overrides,
                        inactivity_timeout=CANCEL_IDLE_SECONDS,
                    )
                cancelled = await client.cancel(idle.value.continuation)
                assert cancelled.was_interrupted
                assert not cancelled.was_completed

    asyncio.run(run())
