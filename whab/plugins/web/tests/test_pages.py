"""Bundled Web pages, visual assets and escaped user content."""

from whab import __version__
from whab.plugins import Insight, Plugin


def test_all_pages_render_and_escape_user_content(client):
    for page in ("budget", "transactions", "insights", "accounts"):
        response = client.get("/", params={"page": page, "workspace": "demo", "month": "2026-10"})
        assert response.status_code == 200
        assert "Sample data" in response.text
        assert "/static/app.js" in response.text
        assert "WHAB · We Have A Budget" in response.text
        assert f"WHAB · {__version__}" in response.text
        assert 'id="whab-context"' in response.text
    assert client.app.version == __version__
    name = '<script>alert("x")</script>'
    assert (
        client.post(
            "/api/accounts",
            json={"workspace": "personal", "name": name, "type": "depository", "balance": 10000},
        ).status_code
        == 200
    )
    text = client.get("/?page=accounts&workspace=personal").text
    assert name not in text
    assert "&lt;script&gt;" in text
    assert "Sandbox mode" in text


def test_extension_insights_render_and_escape_content(store, tmp_path, client_for):
    def insights(_snapshot):
        return (Insight("<b>Heading</b>", "<script>value</script>"),)

    def fail(_snapshot):
        raise RuntimeError("FICTIONAL_SECRET_DO_NOT_EXPOSE")

    with client_for(
        store,
        tmp_path,
        Plugin("test", "Test report", insights=insights),
        Plugin("broken", "Broken report", insights=fail),
    ) as client:
        response = client.get("/?page=insights&workspace=demo&month=2026-10")
        assert response.status_code == 200
        assert 'class="plugin-metrics"' in response.text
        assert "&lt;script&gt;value&lt;/script&gt;" in response.text
        assert "<script>value</script>" not in response.text
        assert "could not be generated" in response.text
        assert "FICTIONAL_SECRET" not in response.text
