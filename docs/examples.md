# Examples

All examples live in `examples/`.

## `chat_steps_rich.py`

Rich step-stream output with continuation/cancel flow.

```bash
uv run python examples/chat_steps_rich.py
uv run python examples/chat_steps_rich.py --transport websocket --url ws://127.0.0.1:8765
uv run python examples/chat_steps_rich.py --show-data
uv run python examples/chat_steps_rich.py --cancel-on-timeout
```

## `thread_config_and_fork.py`

Thread config defaults + forked thread workflow.

```bash
uv run python examples/thread_config_and_fork.py --transport stdio
uv run python examples/thread_config_and_fork.py --transport websocket --url ws://127.0.0.1:8765
uv run python examples/thread_config_and_fork.py --quiet
```

## `thread_resume_by_id.py`

Resume existing thread id and continue conversation.

```bash
uv run python examples/thread_resume_by_id.py --thread-id <existing-thread-id>
```

## `thread_concurrent_handles.py`

Runs two fresh `ThreadHandle`s concurrently over one shared client connection.

```bash
uv run python examples/thread_concurrent_handles.py --transport stdio
```

## `thread_ops_showcase.py`

Shows thread/model/config operation outputs.

```bash
uv run python examples/thread_ops_showcase.py
uv run python examples/thread_ops_showcase.py --show-data
uv run python examples/thread_ops_showcase.py --set-model gpt-5.3-codex
```

## `chat_session_stdio.py` and `chat_session_websocket.py`

Multi-turn session examples per transport.
