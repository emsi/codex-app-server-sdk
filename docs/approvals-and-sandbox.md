# Approval requests and sandbox policies

## Related API

- [`ThreadConfig`](api/models.md#codex_app_server_sdk.models.ThreadConfig)
- [`TurnOverrides`](api/models.md#codex_app_server_sdk.models.TurnOverrides)
- [`SandboxMode`](api/models.md#codex_app_server_sdk.models.SandboxMode)
- [`SandboxPolicy`](api/models.md#codex_app_server_sdk.models.SandboxPolicy)
- [`ApprovalPolicy`](api/models.md#codex_app_server_sdk.models.ApprovalPolicy)
- [`CodexClient.set_approval_handler(...)`](api/client.md#codex_app_server_sdk.client.CodexClient.set_approval_handler)
- [`CodexClient.approval_requests(...)`](api/client.md#codex_app_server_sdk.client.CodexClient.approval_requests)
- [`CodexClient.respond_approval(...)`](api/client.md#codex_app_server_sdk.client.CodexClient.respond_approval)
- [`CodexClient.approve_approval(...)`](api/client.md#codex_app_server_sdk.client.CodexClient.approve_approval)
- [`CodexClient.decline_approval(...)`](api/client.md#codex_app_server_sdk.client.CodexClient.decline_approval)
- [`CodexClient.cancel_approval(...)`](api/client.md#codex_app_server_sdk.client.CodexClient.cancel_approval)

## Thread-level policy and sandbox mode

`ThreadConfig` controls defaults persisted for subsequent turns on that thread.

```python
from codex_app_server_sdk import ThreadConfig

cfg = ThreadConfig(
    approval_policy="on-request",
    sandbox="workspace-write",
)
```

`approval_policy` values:

- `untrusted`
- `on-failure` (deprecated by protocol, still accepted)
- `on-request`
- `never`

`sandbox` values:

- `read-only`
- `workspace-write`
- `danger-full-access`

## Turn-level sandbox policy

Use `TurnOverrides.sandbox_policy` for per-turn policy payloads.

```python
from codex_app_server_sdk import TurnOverrides

turn = TurnOverrides(
    sandbox_policy={
        "type": "workspaceWrite",
        "networkAccess": False,
        "writableRoots": ["/tmp"],
    },
    approval_policy="on-request",
)
```

`TurnOverrides.sandbox_policy` accepts either:

- typed `SandboxPolicy` structures, or
- raw mapping payloads (for compatibility with server extensions).

## Approval request handling

The client handles v2 server-initiated approval requests:

- `item/commandExecution/requestApproval`
- `item/fileChange/requestApproval`

If no handler is registered, the SDK auto-responds with `decline` (continue turn).

### Callback mode

```python
from codex_app_server_sdk import (
    CodexClient,
    CommandApprovalRequest,
    CommandApprovalWithExecpolicyAmendment,
    FileChangeApprovalRequest,
)


async def handler(req):
    if isinstance(req, CommandApprovalRequest):
        if req.reason and "network" in req.reason.lower():
            return "decline"
        return CommandApprovalWithExecpolicyAmendment(["uv", "run"])

    if isinstance(req, FileChangeApprovalRequest):
        return "accept_for_session"

    return "decline"


async with CodexClient.connect_stdio() as client:
    client.set_approval_handler(handler)
    result = await client.chat_once("Run a command that may request approval.")
    print(result.final_text)
```

### Stream mode (manual response)

```python
from codex_app_server_sdk import CodexClient, CommandApprovalRequest


async with CodexClient.connect_stdio() as client:
    async for req in client.approval_requests():
        if isinstance(req, CommandApprovalRequest):
            await client.approve_approval(req, for_session=True)
        else:
            await client.decline_approval(req)
```

`approval_requests()` is observational. If a callback is configured, callback handling remains authoritative.
