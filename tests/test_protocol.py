from codex_app_server_client.protocol import (
    extract_error,
    make_error_response,
    make_request,
)


def test_make_request_builds_expected_envelope() -> None:
    payload = make_request(7, "initialize", {"foo": "bar"})
    assert payload["jsonrpc"] == "2.0"
    assert payload["id"] == 7
    assert payload["method"] == "initialize"
    assert payload["params"] == {"foo": "bar"}


def test_extract_error_reads_error_payload() -> None:
    response = make_error_response(9, -32000, "boom", {"x": 1})
    error = extract_error(response)
    assert error is not None
    assert error["code"] == -32000
    assert error["message"] == "boom"
