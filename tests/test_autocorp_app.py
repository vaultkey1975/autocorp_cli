import argparse

from fastapi.testclient import TestClient

import autocorp
import config
from app.server import app as fastapi_app


def test_app_command_registered():
    parser = autocorp.build_parser()
    args = parser.parse_args(["app"])
    assert args.func is autocorp.cmd_app
    assert args.host is None
    assert args.port is None


def test_localhost_is_default_binding():
    assert config.APP_HOST == "127.0.0.1"
    assert config.APP_PORT == 8787


def test_external_binding_is_not_enabled_silently(capsys):
    args = argparse.Namespace(host="0.0.0.0", port=8787, allow_external=False)
    rc = autocorp.cmd_app(args)
    assert rc == 2
    assert "allow-external" in capsys.readouterr().err


def test_health_endpoint_is_fast_and_truthful():
    client = TestClient(fastapi_app)
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["server_ready"] is True
    assert "clonecast" not in body


def test_main_page_loads():
    client = TestClient(fastapi_app)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "AutoCorp" in resp.text


def test_static_assets_load():
    client = TestClient(fastapi_app)
    css = client.get("/static/app.css")
    js = client.get("/static/app.js")
    assert css.status_code == 200
    assert js.status_code == 200
    assert "text/css" in css.headers["content-type"]


def test_sessions_endpoint_empty_by_default(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    client = TestClient(fastapi_app)
    resp = client.get("/api/sessions")
    assert resp.status_code == 200
    assert resp.json() == []
