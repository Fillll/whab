"""Bulk protocol bounds cover escaped Unicode without widening ordinary forms."""

import json

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from whab.web.middleware import install_middleware


def test_full_unicode_batch_and_chunked_protocol_bounds():
    app = FastAPI()
    install_middleware(app, 5173, remote=True)

    @app.post("/api/local/v1/commands")
    @app.post("/remote/v1/budgets/fictional/commands")
    async def echo(request: Request):
        value = await request.json()
        return {"count": len(value["payload"]["edits"])}

    value = {
        "operation": "edit_transactions",
        "workspace": "personal",
        "payload": {
            "edits": [{"transactionId": f"fictional-{i}", "note": "🍏" * 1000} for i in range(100)]
        },
        "expected_revision": "fictional",
        "request_id": "fictional",
    }
    body = json.dumps(value).encode()
    assert len(body) > 1024 * 1024
    with TestClient(app, base_url="https://127.0.0.1:5173") as client:
        for path in ("/api/local/v1/commands", "/remote/v1/budgets/fictional/commands"):
            assert client.post(
                path, content=body, headers={"content-type": "application/json"}
            ).json() == {"count": 100}

            def chunks():
                yield b" " * (2 * 1024 * 1024)
                yield b"{}"

            assert (
                client.post(
                    path, content=chunks(), headers={"content-type": "application/json"}
                ).status_code
                == 413
            )
            assert (
                client.post(
                    path,
                    content=b"{}",
                    headers={
                        "content-type": "application/json",
                        "content-length": str(2 * 1024 * 1024 + 1),
                    },
                ).status_code
                == 413
            )
        assert (
            client.post(
                "/api/ordinary",
                content=b" " * (32 * 1024 + 1),
                headers={"content-type": "application/json"},
            ).status_code
            == 413
        )
