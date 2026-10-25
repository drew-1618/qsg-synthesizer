"""Installed-app (PWA) files: manifest, service worker and offline page."""

import json
import re
from pathlib import Path

from fastapi.testclient import TestClient

from qsg_synthesizer.main import STATIC_DIR, app

client = TestClient(app)


def test_service_worker_is_served_from_the_root_uncached():
    response = client.get("/sw.js")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/javascript")
    assert response.headers["cache-control"] == "no-cache"


def test_manifest_is_installable():
    response = client.get("/manifest.webmanifest")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/manifest+json")
    manifest = response.json()
    assert manifest["start_url"] == "/" and manifest["scope"] == "/"
    assert manifest["display"] == "standalone"
    sizes = {icon["sizes"] for icon in manifest["icons"] if icon["purpose"] == "any"}
    assert {"192x192", "512x512"} <= sizes
    assert any(icon["purpose"] == "maskable" for icon in manifest["icons"])
    for icon in manifest["icons"]:
        assert client.get(icon["src"]).status_code == 200, icon["src"]


def test_every_file_the_worker_caches_exists():
    worker = (STATIC_DIR / "sw.js").read_text(encoding="utf-8")
    block = re.search(r"OFFLINE_ASSETS = \[(.*?)\];", worker, re.DOTALL).group(1)
    assets = re.findall(r'"(/static/[^"]+)"', block) + ["/static/offline.html"]
    assert len(assets) > 3
    for asset in assets:
        assert client.get(asset).status_code == 200, asset


def test_dashboard_links_the_manifest():
    html = client.get("/").text
    assert 'rel="manifest" href="/manifest.webmanifest"' in html


def test_offline_page_only_uses_cached_assets():
    # It renders while the server is down, so anything it loads must be cached.
    page = (STATIC_DIR / "offline.html").read_text(encoding="utf-8")
    worker = (STATIC_DIR / "sw.js").read_text(encoding="utf-8")
    for url in re.findall(r'(?:href|src)="(/static/[^"]+)"', page):
        assert f'"{url}"' in worker, url


def test_pwa_routes_require_auth_once_configured(monkeypatch):
    monkeypatch.setenv("APP_USERNAME", "admin")
    monkeypatch.setenv("APP_PASSWORD", "hunter2")
    assert client.get("/sw.js").status_code == 401
    assert client.get("/manifest.webmanifest").status_code == 401


def test_manifest_is_valid_json_on_disk():
    json.loads(Path(STATIC_DIR / "manifest.webmanifest").read_text(encoding="utf-8"))


def test_dashboard_and_static_files_are_revalidated_each_load():
    for path in ("/", "/static/app.js", "/static/tailwind.css", "/static/offline.html"):
        assert client.get(path).headers["cache-control"] == "no-cache", path
