# Human-in-the-loop: plan mode and user questions

## Related API

- [`TurnOverrides`](api/models.md#codex_app_server_sdk.models.TurnOverrides)
- [`CollaborationMode`](api/models.md#codex_app_server_sdk.models.CollaborationMode)
- [`CollaborationSettings`](api/models.md#codex_app_server_sdk.models.CollaborationSettings)
- [`CodexClient.set_user_input_handler(...)`](api/client.md#codex_app_server_sdk.client.CodexClient.set_user_input_handler)
- [`CodexClient.user_input_requests(...)`](api/client.md#codex_app_server_sdk.client.CodexClient.user_input_requests)
- [`CodexClient.respond_user_input(...)`](api/client.md#codex_app_server_sdk.client.CodexClient.respond_user_input)
- [`CodexClient.respond_user_input_choice(...)`](api/client.md#codex_app_server_sdk.client.CodexClient.respond_user_input_choice)
- [`CodexClient.respond_user_input_other(...)`](api/client.md#codex_app_server_sdk.client.CodexClient.respond_user_input_other)

## Running a turn in plan mode

Use [`TurnOverrides.collaboration_mode`](api/models.md#codex_app_server_sdk.models.TurnOverrides)
with [`chat_once(...)`](api/client.md#codex_app_server_sdk.client.CodexClient.chat_once)
or [`chat(...)`](api/client.md#codex_app_server_sdk.client.CodexClient.chat):

```python
from codex_app_server_sdk import (
    CodexClient,
    CollaborationMode,
    CollaborationSettings,
    TurnOverrides,
)

async with CodexClient.connect_stdio() as client:
    result = await client.chat_once(
        "Design a migration plan.",
        turn_overrides=TurnOverrides(
            collaboration_mode=CollaborationMode(
                mode="plan",
                settings=CollaborationSettings(
                    model="gpt-5.3-codex",
                    reasoning_effort="high",
                    developer_instructions=None,
                ),
            )
        ),
    )
    print(result.final_text)
```

## Answering user-input questions (callback mode)

`item/tool/requestUserInput` requests are surfaced as
[`UserInputRequest`](api/models.md#codex_app_server_sdk.models.UserInputRequest).

```python
from codex_app_server_sdk import CodexClient, UserInputAnswer, UserInputResponse

async def user_input_handler(request):
    # Example: approve plan questions by selecting "yes".
    return UserInputResponse(
        answers={
            "plan_confirmation": UserInputAnswer(answers=["yes"]),
        }
    )

async with CodexClient.connect_stdio() as client:
    client.set_user_input_handler(user_input_handler)
    result = await client.chat_once("Create a plan and ask for confirmation.")
    print(result.final_text)
```

## Answering user-input questions (stream/manual mode)

For manual control, run chat and user-input handling concurrently:

```python
import asyncio
from contextlib import suppress
from codex_app_server_sdk import CodexClient

async with CodexClient.connect_stdio(user_input_response_timeout=None) as client:
    async def user_input_loop():
        async for req in client.user_input_requests():
            # Example: choose one option
            await client.respond_user_input_choice(
                req,
                question_id=req.questions[0].id,
                selections=["yes"],
            )

    task = asyncio.create_task(user_input_loop())
    try:
        result = await client.chat_once(
            "Plan the changes and ask me to confirm before executing."
        )
        print(result.final_text)
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
```

## Notes

- If `set_user_input_handler(...)` is not configured, unanswered requests fail after `user_input_response_timeout`.
- Set `user_input_response_timeout=None` for fully manual workflows that answer via `user_input_requests()`.

## Handling approvals and user-input together

If a turn can trigger both approval requests and user-input requests, run both
streams concurrently while awaiting `chat_once(...)` or consuming `chat(...)`.

```python
import asyncio
from contextlib import suppress
from codex_app_server_sdk import CodexClient, CommandApprovalRequest

async with CodexClient.connect_stdio(user_input_response_timeout=None) as client:
    async def approvals_loop():
        async for req in client.approval_requests():
            if isinstance(req, CommandApprovalRequest):
                await client.approve_approval(req, for_session=True)
            else:
                await client.decline_approval(req)

    async def user_input_loop():
        async for req in client.user_input_requests():
            await client.respond_user_input_choice(
                req,
                question_id=req.questions[0].id,
                selections=["yes"],
            )

    approvals_task = asyncio.create_task(approvals_loop())
    input_task = asyncio.create_task(user_input_loop())
    try:
        result = await client.chat_once("Plan and execute after confirmation.")
        print(result.final_text)
    finally:
        approvals_task.cancel()
        input_task.cancel()
        with suppress(asyncio.CancelledError):
            await approvals_task
        with suppress(asyncio.CancelledError):
            await input_task
```
