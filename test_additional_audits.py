from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest

import jarvis_audit
import fb_ad_downloader_daily_audit
import slack_extension_daily_audit


def test_jarvis_check_health_ok(monkeypatch):
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.__enter__.return_value = mock_resp
    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout: mock_resp)
    res = jarvis_audit.check_health()
    assert res["status"] == "ok"
    assert res["code"] == 200


def test_jarvis_check_health_error(monkeypatch):
    def raise_err(req, timeout):
        raise Exception("Connection refused")
    monkeypatch.setattr("urllib.request.urlopen", raise_err)
    res = jarvis_audit.check_health()
    assert res["status"] == "offline_or_error"
    assert "Connection refused" in res["error"]


def test_jarvis_inspect_logs_with_errors(tmp_path, monkeypatch):
    log_file = tmp_path / "server.log"
    log_file.write_text("INFO: starting\nERROR: database locked\nCRITICAL: crash\n", encoding="utf-8")
    monkeypatch.setattr(jarvis_audit, "JARVIS_LOG", log_file)
    res = jarvis_audit.inspect_logs()
    assert res["errors_count"] == 2
    assert len(res["sample_errors"]) == 2


def test_fb_ad_downloader_check_node_syntax_ok(tmp_path, monkeypatch):
    dummy = tmp_path / "dummy.js"
    dummy.write_text("console.log('test');", encoding="utf-8")
    mock_res = MagicMock(returncode=0, stdout="", stderr="")
    monkeypatch.setattr("subprocess.run", lambda *a, **kw: mock_res)
    ok, msg = fb_ad_downloader_daily_audit.check_node_syntax(dummy)
    assert ok is True
    assert msg == "Sintaxe OK"


def test_fb_ad_downloader_check_node_syntax_error(tmp_path, monkeypatch):
    dummy = tmp_path / "dummy.js"
    dummy.write_text("invalid syntax", encoding="utf-8")
    mock_res = MagicMock(returncode=1, stdout="", stderr="SyntaxError: Unexpected token")
    monkeypatch.setattr("subprocess.run", lambda *a, **kw: mock_res)
    ok, msg = fb_ad_downloader_daily_audit.check_node_syntax(dummy)
    assert ok is False
    assert "SyntaxError" in msg


def test_fb_ad_downloader_audit_manifest_v3(tmp_path, monkeypatch):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({
        "manifest_version": 3,
        "name": "FB Downloader",
        "permissions": ["activeTab", "downloads"],
        "host_permissions": ["https://*.facebook.com/*", "https://*.fbcdn.net/*"]
    }), encoding="utf-8")
    monkeypatch.setattr(fb_ad_downloader_daily_audit, "EXTENSION_DIR", tmp_path)
    monkeypatch.setattr(fb_ad_downloader_daily_audit, "check_node_syntax", lambda p: (True, "Sintaxe OK"))
    res = fb_ad_downloader_daily_audit.audit_extension()
    assert res["manifest"]["version"] == "✅ Manifest V3"
    assert "Meta/Facebook OK" in res["manifest"]["hosts"]


def test_slack_extension_audit_manifest_v3(tmp_path, monkeypatch):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({
        "manifest_version": 3,
        "name": "Slack Active",
        "permissions": ["storage", "alarms"],
        "host_permissions": ["https://*.slack.com/*"]
    }), encoding="utf-8")
    monkeypatch.setattr(slack_extension_daily_audit, "EXTENSION_DIR", tmp_path)
    monkeypatch.setattr(slack_extension_daily_audit, "check_node_syntax", lambda p: (True, "Sintaxe OK"))
    res = slack_extension_daily_audit.audit_slack_extension()
    assert res["manifest"]["version"] == "✅ Manifest V3"
    assert "storage=✅" in res["manifest"]["permissions"]
    assert "Restrito a *.slack.com" in res["manifest"]["hosts"]
