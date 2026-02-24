# Protocol mapping

High-level methods map to JSON-RPC methods as follows.

| High-level API | JSON-RPC method |
| --- | --- |
| `initialize(...)` | `initialize` |
| `start_thread(...)` | `thread/start` |
| `resume_thread(...)` | `thread/resume` |
| `fork_thread(...)` | `thread/fork` |
| `set_thread_name(...)` | `thread/name/set` |
| `archive_thread(...)` | `thread/archive` |
| `unarchive_thread(...)` | `thread/unarchive` |
| `compact_thread(...)` | `thread/compact/start` |
| `rollback_thread(...)` | `thread/rollback` |
| `read_thread(...)` | `thread/read` |
| `list_threads(...)` | `thread/list` |
| `chat_once(...)` / `chat(...)` turn start path | `turn/start` |
| `interrupt_turn(...)` / `cancel(...)` | `turn/interrupt` |
| `steer_turn(...)` | `turn/steer` |
| `start_review(...)` | `review/start` |
| `list_models(...)` | `model/list` |
| `exec_command(...)` | `command/exec` |
| `read_config(...)` | `config/read` |
| `read_config_requirements(...)` | `configRequirements/read` |
| `write_config_value(...)` | `config/value/write` |
| `batch_write_config(...)` | `config/batchWrite` |

## Notification handling

- step extraction is based on `item/completed`
- turn completion/failure uses supported aliases (`turn/completed`, `turn/failed`, etc.)
- transport-level receive loop routes responses vs notifications
