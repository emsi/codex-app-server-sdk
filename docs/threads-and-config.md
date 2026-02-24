# Threads and configuration

## Scope boundaries

- `CodexClient`: connection/session scope
- `ThreadHandle` + `ThreadConfig`: thread scope
- `TurnOverrides`: per-turn scope

## `UNSET` vs `None`

- `UNSET`: omit field from request payload
- `None`: send explicit JSON `null` (where allowed by protocol)

```python
from codex_app_server_client import ThreadConfig, UNSET

cfg = ThreadConfig(
    model=UNSET,
    developer_instructions=None,
)
```

## Thread lifecycle

High-level methods:

- `start_thread(config=None)`
- `resume_thread(thread_id, overrides=None)`
- `fork_thread(thread_id, overrides=None)`
- `read_thread(thread_id, include_turns=True)`
- `list_threads(...)`
- `set_thread_name(...)`
- `archive_thread(...)` / `unarchive_thread(...)`
- `compact_thread(...)`
- `rollback_thread(...)`

`ThreadHandle` wraps these operations for one thread id.

## Setting model/cwd/instructions

Thread-level defaults belong on thread methods (`thread/start`, `thread/resume`, `thread/fork`).
Per-turn changes belong in `TurnOverrides`.

For persistent thread behavior:

```python
thread = await client.start_thread(
    ThreadConfig(
        cwd=".",
        base_instructions="Be concise",
        developer_instructions="Focus on correctness",
        model="gpt-5.3-codex",
    )
)
```
