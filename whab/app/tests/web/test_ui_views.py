from fastapi import FastAPI
from fastapi.testclient import TestClient

from whab.plugins.ui_views import BrowserViews
from whab.web.errors import install_error_handlers
from whab.web.middleware import install_middleware
from whab.web.routes.ui_views import create_router


def client(views):
    app = FastAPI()
    install_error_handlers(app)
    install_middleware(app, 8000)
    app.include_router(create_router(lambda: views))
    return TestClient(app, base_url="https://localhost:8000")


def test_browser_state_is_bounded_opaque_and_origin_guarded():
    views = BrowserViews("a" * 64)
    with client(views) as http:
        body = {"generation": "a" * 64, "dirty": True}
        registered = http.post("/api/ui/views", json=body)
        assert registered.status_code == 201
        identifier = registered.json()["view"]
        assert registered.json()["reload"] is False
        assert http.put(f"/api/ui/views/{identifier}", json={**body, "dirty": False}).status_code == 200
        assert http.post("/api/ui/views", json={**body, "draft": "private"}).status_code == 400
        assert http.post("/api/ui/views", json={**body, "dirty": 1}).status_code == 400
        assert http.post("/api/ui/views", json=body, headers={"Origin": "https://other.invalid"}).status_code == 403
        assert http.post("/api/ui/views", json={**body, "prepared": "p"}).status_code == 400
        assert http.delete(f"/api/ui/views/{identifier}").status_code == 204
        unknown = http.put(f"/api/ui/views/{identifier}", json=body)
        assert unknown.status_code == 400 and unknown.json()["code"] == "ui_view_unknown"
        assert http.put("/api/ui/views/not-a-uuid", json=body).status_code == 400


def test_headless_budget_has_no_browser_participation():
    with client(None) as http:
        assert http.post("/api/ui/views", json={"generation": "a" * 64, "dirty": False}).status_code == 404
