# Transports

The client supports two transport implementations:

- `StdioTransport`: line-delimited JSON over subprocess stdin/stdout
- `WebSocketTransport`: JSON envelopes over websocket frames

## Stdio transport

Factory:

```python
CodexClient.connect_stdio(
    command=["codex", "app-server"],
    cwd=None,
    env=None,
)
```

Defaults:

- command: `codex app-server`
- can be overridden with `CODEX_APP_SERVER_CMD`

## Websocket transport

Factory:

```python
CodexClient.connect_websocket(
    url="ws://127.0.0.1:8765",
    token=None,
    headers=None,
)
```

Defaults:

- URL: `CODEX_APP_SERVER_WS_URL` or `ws://127.0.0.1:8765`
- token: optional `CODEX_APP_SERVER_TOKEN`
- compression: disabled by default (`None`) in the transport implementation

## Lifecycle

Preferred pattern:

```python
async with CodexClient.connect_stdio() as client:
    ...
```

Equivalent manual pattern:

```python
client = CodexClient.connect_stdio()
await client.start()
try:
    ...
finally:
    await client.close()
```
