"""refresh_email.send_report: verified STARTTLS, and only Exception is swallowed. No network."""
import datetime
import ssl
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import refresh_email as mod


class FakeSMTP:
    instances = []
    login_error = None

    def __init__(self, host, port, timeout=None):
        self.host, self.port, self.calls = host, port, []
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def ehlo(self):
        self.calls.append(("ehlo", (), {}))

    def starttls(self, *args, **kwargs):
        self.calls.append(("starttls", args, kwargs))

    def login(self, user, password):
        if FakeSMTP.login_error is not None:
            raise FakeSMTP.login_error
        self.calls.append(("login", (user,), {}))

    def send_message(self, msg):
        self.calls.append(("send_message", (msg,), {}))


@pytest.fixture
def smtp(monkeypatch, tmp_path):
    FakeSMTP.instances, FakeSMTP.login_error = [], None
    monkeypatch.setattr(mod.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(mod, "_credentials", lambda: ("id@example.com", "app-password", "from@example.com"))
    log = tmp_path / "refresh.log"
    log.write_text("===== WEEKLY REFRESH START =====\nrank movement: none\n", encoding="utf-8")
    return log


def send(log):
    return mod.send_report(str(log), datetime.datetime.now(), "SUCCESS")


def test_starttls_uses_a_verifying_ssl_context(smtp):
    assert send(smtp) is True
    (client,) = FakeSMTP.instances
    assert (client.host, client.port) == ("smtp.mail.me.com", 587)
    (starttls,) = [c for c in client.calls if c[0] == "starttls"]
    context = starttls[2].get("context")
    assert isinstance(context, ssl.SSLContext)
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname is True
    assert [c[0] for c in client.calls] == ["ehlo", "starttls", "ehlo", "login", "send_message"]


def test_ordinary_failure_is_logged_not_raised(smtp):
    FakeSMTP.login_error = OSError("connection reset")
    assert send(smtp) is False
    assert "EMAIL REPORT FAILED - OSError" in smtp.read_text(encoding="utf-8")


@pytest.mark.parametrize("signal", [KeyboardInterrupt, SystemExit])
def test_interpreter_exit_signals_are_not_swallowed(smtp, signal):
    FakeSMTP.login_error = signal()
    with pytest.raises(signal):
        send(smtp)


def test_missing_credentials_skips_without_connecting(smtp, monkeypatch):
    monkeypatch.setattr(mod, "_credentials", lambda: None)
    assert send(smtp) is False
    assert FakeSMTP.instances == [] and "EMAIL REPORT SKIPPED" in smtp.read_text(encoding="utf-8")
