"""Offline regressions for export safety, rendering, and delivery contracts."""
import importlib.util
from pathlib import Path
import ssl
import sys
from datetime import datetime, timezone
from unittest.mock import MagicMock

import httpx
import pytest

spec = importlib.util.spec_from_file_location(
    "dive_export", Path(__file__).resolve().parents[1] / "flight-plans/flight-dive-export/flight.py"
)
flight = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = flight
spec.loader.exec_module(flight)


@pytest.fixture
def export():
    return flight.Export("test", "https://example.com", datetime.now(timezone.utc),
                         "Report", "Ready", [flight.Rendition("pdf", "test.pdf", "application/pdf", b"%PDF-test")])


@pytest.mark.parametrize("tls", ["ssl", "starttls"])
def test_smtp_authenticates_server(monkeypatch, export, tls):
    monkeypatch.setenv("SMTP_TLS", tls)
    monkeypatch.setenv("EMAIL_TO", "test@example.com")
    monkeypatch.setenv("EMAIL_FROM", "sender@example.com")
    smtp = MagicMock()
    smtp.send_message.return_value = {}
    factory = MagicMock(return_value=smtp)
    monkeypatch.setattr(flight.smtplib, "SMTP_SSL" if tls == "ssl" else "SMTP", factory)
    flight.deliver_email(export, False)
    context = (factory.call_args.kwargs if tls == "ssl" else smtp.starttls.call_args.kwargs)["context"]
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname
    smtp.send_message.return_value = {"test@example.com": (550, b"refused")}
    with pytest.raises(RuntimeError, match="refused"):
        flight.deliver_email(export, False)


def test_invalid_dry_run_stops_before_side_effects(monkeypatch):
    monkeypatch.setenv("DRY_RUN", "tru")
    install = MagicMock()
    monkeypatch.setattr(flight, "install_chromium", install)
    with pytest.raises(ValueError, match="DRY_RUN"):
        flight.main()
    install.assert_not_called()


def test_api_origin_cannot_be_overridden(monkeypatch):
    monkeypatch.setenv("API_BASE", "https://attacker.example")
    spec.loader.exec_module(flight)
    post = MagicMock(return_value=httpx.Response(200, json={"session": "test"}, request=httpx.Request("POST", "https://api.motherduck.com")))
    monkeypatch.setattr(flight.httpx, "post", post)
    flight.mint_embed_session("test", "service", "token")
    assert post.call_args.args[0] == "https://api.motherduck.com/v1/dives/test/embed-session"


def test_text_readiness_observes_minimum_wait(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(flight.time, "monotonic", lambda: now[0])
    page = MagicMock()
    page.wait_for_timeout.side_effect = lambda ms: now.__setitem__(0, now[0] + ms / 1000)
    page.evaluate.return_value = True
    flight.settle(page, flight.Wait(15000, 20000, "Ready"))
    assert now[0] >= 15


@pytest.mark.parametrize("kind", ["png", "pdf"])
def test_capture_only_selected_format(monkeypatch, kind):
    import playwright.sync_api
    play = MagicMock()
    page = play.chromium.launch.return_value.new_page.return_value
    page.screenshot.return_value = b"png"
    page.pdf.return_value = b"pdf"
    manager = MagicMock()
    manager.__enter__.return_value = play
    monkeypatch.setattr(playwright.sync_api, "sync_playwright", lambda: manager)
    monkeypatch.setattr(flight, "settle", MagicMock())
    monkeypatch.setattr(flight, "check_rendered", MagicMock())
    monkeypatch.setattr(flight, "grow_viewport", lambda *args: 1000)
    assert flight.capture("https://example.com", flight.Wait(0, 1, ""), (1440, 1000), 1, 0, [kind]) == {kind: kind.encode()}
    if kind == "png":
        page.pdf.assert_not_called()
    else:
        page.screenshot.assert_not_called()
        page.emulate_media.assert_called_once_with(media="screen")


def test_graph_requires_completed_upload(monkeypatch, export):
    monkeypatch.setattr(flight.httpx, "post", lambda *a, **kw: httpx.Response(200, json={"uploadUrl": "https://example.com/upload"}))
    monkeypatch.setattr(flight.httpx, "put", lambda *a, **kw: httpx.Response(202, json={"nextExpectedRanges": ["0-"]}))
    with pytest.raises(RuntimeError, match="did not complete"):
        flight.graph_upload("token", "drive", "folder", export.renditions[0])


@pytest.mark.parametrize("viewport", ["0x1000", "1440x0", "1440x30001"])
def test_invalid_viewport(viewport):
    with pytest.raises(ValueError):
        flight.parse_viewport(viewport)


def test_navigation_failure_does_not_expose_session(monkeypatch):
    import playwright.sync_api
    play = MagicMock()
    page = play.chromium.launch.return_value.new_page.return_value
    page.goto.side_effect = RuntimeError("navigation failed #session=secret")
    manager = MagicMock()
    manager.__enter__.return_value = play
    monkeypatch.setattr(playwright.sync_api, "sync_playwright", lambda: manager)
    with pytest.raises(RuntimeError, match="Could not navigate") as error:
        flight.capture("https://example.com/#session=secret", flight.Wait(0, 1, ""), (800, 600), 1, 0, ["png"])
    assert "secret" not in str(error.value)
    assert error.value.__suppress_context__


def test_failed_target_does_not_block_others_or_log_webhook(monkeypatch, capsys):
    monkeypatch.setenv("SHOT_URL", "https://example.com")
    monkeypatch.setenv("STORE_TABLE", "")
    monkeypatch.setenv("DELIVERY", "teams,email")
    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.setattr(flight, "check_delivery_config", MagicMock())
    monkeypatch.setattr(flight, "install_chromium", MagicMock())
    monkeypatch.setattr(flight, "capture", lambda *args: {"pdf": b"pdf", "png": b"png"})
    response = httpx.Response(403, request=httpx.Request("POST", "https://example.com/?sig=secret"))
    failed = MagicMock(side_effect=httpx.HTTPStatusError("secret", request=response.request, response=response))
    succeeded = MagicMock()
    monkeypatch.setattr(flight, "DELIVERY_TARGETS", {"teams": {"deliver": failed}, "email": {"deliver": succeeded}})
    with pytest.raises(RuntimeError, match="teams"):
        flight.main()
    succeeded.assert_called_once()
    assert "secret" not in capsys.readouterr().out
