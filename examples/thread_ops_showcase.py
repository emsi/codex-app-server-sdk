#!/usr/bin/env python3
"""Showcase thread/model/config helper APIs over stdio or websocket transport."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shlex
import sys
from collections.abc import Mapping
from typing import Any

from codex_app_server_client import (
    CodexClient,
    CodexProtocolError,
    CodexTimeoutError,
    CodexTransportError,
    ThreadConfig,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--transport",
        choices=["stdio", "websocket"],
        default="stdio",
        help="Transport mode.",
    )
    parser.add_argument(
        "--cmd",
        default="codex app-server",
        help="Stdio app-server command.",
    )
    parser.add_argument(
        "--url",
        default=os.getenv("CODEX_APP_SERVER_WS_URL", "ws://127.0.0.1:8765"),
        help="Websocket URL.",
    )
    parser.add_argument(
        "--token",
        default=os.getenv("CODEX_APP_SERVER_TOKEN"),
        help="Optional websocket bearer token.",
    )
    parser.add_argument(
        "--cwd",
        default=None,
        help="Optional thread cwd.",
    )
    parser.add_argument(
        "--prompt",
        default="Give a 3-bullet summary of the best life-hacks for improving productivity.",
        help="Prompt to send on the started thread.",
    )
    parser.add_argument(
        "--thread-name",
        default="api-showcase-thread",
        help="Name to assign to the started thread.",
    )
    parser.add_argument(
        "--list-limit",
        type=int,
        default=5,
        help="Max threads to fetch via thread/list.",
    )
    parser.add_argument(
        "--models-limit",
        type=int,
        default=5,
        help="Max models to fetch via model/list.",
    )
    parser.add_argument(
        "--set-model",
        default=None,
        help="Optional model id to apply on the thread (shows before/after model).",
    )
    parser.add_argument(
        "--archive",
        action="store_true",
        help="Archive the started thread at the end.",
    )
    parser.add_argument(
        "--show-data",
        action="store_true",
        help="Print raw JSON payloads from helper APIs.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress lifecycle progress logs.",
    )
    return parser.parse_args()


def _build_client(args: argparse.Namespace) -> CodexClient:
    if args.transport == "websocket":
        return CodexClient.connect_websocket(url=args.url, token=args.token)
    return CodexClient.connect_stdio(command=shlex.split(args.cmd))


def _log(args: argparse.Namespace, message: str) -> None:
    if not args.quiet:
        print(f"[meta] {message}")


def _result_header(name: str, payload: Any, *, show_data: bool) -> bool:
    if not show_data:
        return False
    print(f"[result:{name}]")
    print(json.dumps(payload, indent=2, sort_keys=True))
    return True


def _to_camel_case(key: str) -> str:
    if "_" not in key:
        return key
    parts = [p for p in key.split("_") if p]
    if not parts:
        return key
    head = parts[0]
    tail = "".join(part[:1].upper() + part[1:] for part in parts[1:])
    return head + tail


def _to_snake_case(key: str) -> str:
    out: list[str] = []
    for char in key:
        if char.isupper():
            out.append("_")
            out.append(char.lower())
        else:
            out.append(char)
    candidate = "".join(out).lstrip("_")
    return candidate or key


def _get_field(mapping: Mapping[str, Any], *keys: str) -> tuple[bool, Any]:
    candidates: list[str] = []
    for key in keys:
        if key not in candidates:
            candidates.append(key)
        snake = _to_snake_case(key)
        camel = _to_camel_case(key)
        if snake not in candidates:
            candidates.append(snake)
        if camel not in candidates:
            candidates.append(camel)

    for key in candidates:
        if key in mapping:
            return True, mapping[key]
    return False, None


def _display_value(
    found: bool,
    value: Any,
    *,
    max_len: int | None = None,
    quote_strings: bool = False,
) -> str:
    if not found:
        return "<not-provided>"
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        text = value if max_len is None or len(value) <= max_len else f"{value[: max_len - 3]}..."
        return repr(text) if quote_strings else text
    return str(value)


def _extract_items(payload: Any, *, keys: tuple[str, ...]) -> list[dict[str, Any]]:
    if isinstance(payload, Mapping):
        for key in keys:
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def _extract_thread(payload: Any) -> dict[str, Any] | None:
    if isinstance(payload, Mapping):
        thread = payload.get("thread")
        if isinstance(thread, Mapping):
            return dict(thread)
    return None


def _thread_model(payload: Any) -> str:
    thread = _extract_thread(payload)
    if thread is None:
        return "<not-provided-by-thread/read>"
    found, value = _get_field(thread, "model", "modelId")
    shown = _display_value(found, value)
    if shown == "<not-provided>":
        return "<not-provided-by-thread/read>"
    return shown


def _print_thread_read(payload: Any, *, show_data: bool) -> None:
    if _result_header("thread/read", payload, show_data=show_data):
        return
    thread = _extract_thread(payload)
    if thread is None:
        print("[result:thread/read] unavailable")
        return
    id_found, id_value = _get_field(thread, "id")
    cwd_found, cwd_value = _get_field(thread, "cwd", "workdir")
    provider_found, provider_value = _get_field(thread, "modelProvider", "model_provider")
    source_found, source_value = _get_field(thread, "source")
    updated_found, updated_value = _get_field(thread, "updatedAt", "updated_at")
    preview_found, preview_value = _get_field(thread, "preview")
    name_found, name_value = _get_field(thread, "name", "title")
    model_found, model_value = _get_field(thread, "model", "modelId")
    archived_found, archived_value = _get_field(thread, "archived")
    turns_found, turns_value = _get_field(thread, "turns")
    if turns_found and isinstance(turns_value, list):
        turns_shown = str(len(turns_value))
    else:
        turns_shown = _display_value(turns_found, turns_value)

    line1_parts = [
        f"id={_display_value(id_found, id_value)}",
        f"cwd={_display_value(cwd_found, cwd_value)}",
        f"source={_display_value(source_found, source_value)}",
        f"updated_at={_display_value(updated_found, updated_value)}",
        f"turns={turns_shown}",
    ]
    if not (provider_found and provider_value is None):
        line1_parts.insert(2, f"model_provider={_display_value(provider_found, provider_value)}")
    print("[result:thread/read] " + " ".join(line1_parts))
    print(
        "[result:thread/read] "
        f"preview={_display_value(preview_found, preview_value, max_len=120, quote_strings=True)} "
        f"name={_display_value(name_found, name_value, max_len=80, quote_strings=True)} "
        f"model={_display_value(model_found, model_value)} "
        f"archived={_display_value(archived_found, archived_value)}"
    )


def _print_thread_list(payload: Any, *, limit: int, show_data: bool) -> None:
    if _result_header("thread/list", payload, show_data=show_data):
        return
    items = _extract_items(payload, keys=("data", "threads"))
    next_found, next_value = (
        _get_field(payload, "nextCursor", "next_cursor") if isinstance(payload, Mapping) else (False, None)
    )
    showing = min(limit, len(items))
    print(
        "[result:thread/list] "
        f"count={len(items)} showing={showing} "
        f"next_cursor={_display_value(next_found, next_value, max_len=80, quote_strings=True)}"
    )
    for index, item in enumerate(items[:limit], start=1):
        id_found, id_value = _get_field(item, "id")
        preview_found, preview_value = _get_field(item, "preview")
        cwd_found, cwd_value = _get_field(item, "cwd", "workdir")
        provider_found, provider_value = _get_field(item, "modelProvider", "model_provider")
        updated_found, updated_value = _get_field(item, "updatedAt", "updated_at")
        name_found, name_value = _get_field(item, "name", "title")
        model_found, model_value = _get_field(item, "model", "modelId")
        row_parts = [
            f"id={_display_value(id_found, id_value)}",
            f"preview={_display_value(preview_found, preview_value, max_len=80, quote_strings=True)}",
            f"cwd={_display_value(cwd_found, cwd_value)}",
            f"updated_at={_display_value(updated_found, updated_value)}",
            f"name={_display_value(name_found, name_value, max_len=50, quote_strings=True)}",
            f"model={_display_value(model_found, model_value)}",
        ]
        if not (provider_found and provider_value is None):
            row_parts.insert(3, f"model_provider={_display_value(provider_found, provider_value)}")
        print(f"  {index}. " + " ".join(row_parts))
    if len(items) > limit:
        print(f"  ... (+{len(items) - limit} more)")


def _print_model_list(payload: Any, *, limit: int, show_data: bool) -> None:
    if _result_header("model/list", payload, show_data=show_data):
        return
    items = _extract_items(payload, keys=("data", "models"))
    next_found, next_value = (
        _get_field(payload, "nextCursor", "next_cursor") if isinstance(payload, Mapping) else (False, None)
    )
    showing = min(limit, len(items))
    print(
        "[result:model/list] "
        f"count={len(items)} showing={showing} "
        f"next_cursor={_display_value(next_found, next_value, max_len=80, quote_strings=True)}"
    )
    for index, item in enumerate(items[:limit], start=1):
        id_found, id_value = _get_field(item, "id")
        display_found, display_value = _get_field(item, "displayName", "display_name", "title")
        provider_found, provider_value = _get_field(item, "provider", "modelProvider", "model_provider")
        model_found, model_value = _get_field(item, "model")
        effort_found, effort_value = _get_field(
            item, "defaultReasoningEffort", "default_reasoning_effort"
        )
        default_found, default_value = _get_field(item, "isDefault", "is_default")
        hidden_found, hidden_value = _get_field(item, "hidden")
        upgrade_found, upgrade_value = _get_field(item, "upgrade")
        row_parts = [
            f"id={_display_value(id_found, id_value)}",
            f"display={_display_value(display_found, display_value, quote_strings=True)}",
            f"model={_display_value(model_found, model_value)}",
            f"default_effort={_display_value(effort_found, effort_value)}",
            f"is_default={_display_value(default_found, default_value)}",
            f"hidden={_display_value(hidden_found, hidden_value)}",
            f"upgrade={_display_value(upgrade_found, upgrade_value)}",
        ]
        if not (provider_found and provider_value is None):
            row_parts.insert(3, f"provider={_display_value(provider_found, provider_value)}")
        print(f"  {index}. " + " ".join(row_parts))
    if len(items) > limit:
        print(f"  ... (+{len(items) - limit} more)")


def _print_config_read(payload: Any, *, show_data: bool) -> None:
    if _result_header("config/read", payload, show_data=show_data):
        return
    if not isinstance(payload, dict):
        print(f"[result:config/read] {payload!r}")
        return
    config = payload.get("config")
    origins = payload.get("origins")
    if not isinstance(config, Mapping):
        print(f"[result:config/read] config={config!r}")
        return
    origin_keys: list[str] = []
    if isinstance(origins, Mapping):
        origins_count = len(origins)
        origin_keys = [str(key) for key in origins.keys()]
    elif isinstance(origins, list):
        origins_count = len(origins)
    else:
        origins_count = None
    model_found, model_value = _get_field(config, "model", "modelId")
    provider_found, provider_value = _get_field(config, "model_provider", "modelProvider", "provider")
    approval_found, approval_value = _get_field(config, "approval_policy", "approvalPolicy")
    sandbox_found, sandbox_value = _get_field(config, "sandbox_mode", "sandboxMode", "sandbox")
    personality_found, personality_value = _get_field(config, "personality")
    effort_found, effort_value = _get_field(config, "model_reasoning_effort", "modelReasoningEffort")
    parts = [
        f"model={_display_value(model_found, model_value)}",
        f"approval_policy={_display_value(approval_found, approval_value)}",
        f"sandbox_mode={_display_value(sandbox_found, sandbox_value)}",
        f"personality={_display_value(personality_found, personality_value)}",
        f"reasoning_effort={_display_value(effort_found, effort_value)}",
        f"origin_entries={_display_value(origins_count is not None, origins_count)}",
    ]
    if not (provider_found and provider_value is None):
        parts.insert(1, f"model_provider={_display_value(provider_found, provider_value)}")
    print("[result:config/read] " + " ".join(parts))
    if origins_count is not None:
        if origin_keys:
            sample = ", ".join(origin_keys[:3])
            suffix = ", ..." if len(origin_keys) > 3 else ""
            print(
                "[result:config/read] "
                "origin_entries means how many config keys include provenance "
                f"(where value came from). examples={sample}{suffix}"
            )
        else:
            print(
                "[result:config/read] "
                "origin_entries means how many config keys include provenance "
                "(where value came from)."
            )


async def run(args: argparse.Namespace) -> int:
    try:
        _log(args, f"transport={args.transport} connecting...")
        async with _build_client(args) as client:
            init = await client.initialize()
            _log(args, f"initialized protocol_version={init.protocol_version or 'unknown'}")

            thread_cfg = ThreadConfig(cwd=args.cwd) if args.cwd is not None else None
            _log(args, "starting thread...")
            thread = await client.start_thread(thread_cfg)
            _log(args, f"started thread_id={thread.thread_id}")

            _log(args, f"setting thread name={args.thread_name!r}...")
            name_result = await client.set_thread_name(thread.thread_id, args.thread_name)
            if args.show_data:
                _result_header("thread/name/set", name_result, show_data=True)
            else:
                print(
                    "[result:thread/name/set] "
                    f"name={args.thread_name!r} status={'ok' if name_result is None else name_result!r}"
                )

            if args.set_model is not None:
                _log(args, "reading current thread model before update...")
                before_model_snapshot = await thread.read(include_turns=False)
                before_model = _thread_model(before_model_snapshot)
                print(
                    "[result:thread/model] "
                    f"before={before_model!r} requested={args.set_model!r}"
                )
                _log(args, f"updating thread model={args.set_model!r}...")
                await thread.update_defaults(ThreadConfig(model=args.set_model))
                after_model_snapshot = await thread.read(include_turns=False)
                after_model = _thread_model(after_model_snapshot)
                print(f"[result:thread/model] after={after_model!r}")

            _log(args, f"sending prompt={args.prompt!r}")
            result = await thread.chat_once(args.prompt)
            _log(
                args,
                f"turn complete thread_id={result.thread_id} turn_id={result.turn_id}",
            )
            print(f"[chat] thread_id={result.thread_id} turn_id={result.turn_id}")
            print(result.final_text)

            _log(args, "reading thread snapshot...")
            thread_snapshot = await thread.read(include_turns=True)
            _print_thread_read(thread_snapshot, show_data=args.show_data)
            _log(args, "listing threads...")
            threads_page = await client.list_threads(
                limit=args.list_limit,
                sort_key="updated_at",
                sort_direction="desc",
            )
            _print_thread_list(
                threads_page,
                limit=max(1, args.list_limit),
                show_data=args.show_data,
            )
            _log(args, "listing models...")
            models_page = await client.list_models(limit=args.models_limit)
            _print_model_list(
                models_page,
                limit=max(1, args.models_limit),
                show_data=args.show_data,
            )
            _log(args, "reading config...")
            config_snapshot = await client.read_config(include_layers=False)
            _print_config_read(config_snapshot, show_data=args.show_data)

            if args.archive:
                _log(args, "archiving thread...")
                archive_result = await client.archive_thread(thread.thread_id)
                if args.show_data:
                    _result_header("thread/archive", archive_result, show_data=True)
                else:
                    print(
                        "[result:thread/archive] "
                        f"thread_id={thread.thread_id} "
                        f"status={'ok' if archive_result is None else archive_result!r}"
                    )
                _log(args, f"archived thread_id={thread.thread_id}")
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
    raise SystemExit(asyncio.run(run(parse_args())))


if __name__ == "__main__":
    main()
