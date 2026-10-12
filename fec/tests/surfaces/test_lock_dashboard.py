"""The lock page masks the password as it is typed, still trims it, and the page still decrypts.

Synthetic plaintext and a synthetic password file in tmp; the real dashboard and password are never touched.
"""
import base64
import hashlib
import re
import sys
from pathlib import Path

import pytest
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from surfaces import lock_dashboard as LD

PLAINTEXT = "<!doctype html><title>synthetic</title><p>café — synthetic dashboard body</p>\n"
PASSWORD = "synthetic-pass-123"


@pytest.fixture(scope="module")
def page(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("lock")
    source, password_file, output = tmp / "Moneyball.html", tmp / "pw.txt", tmp / "deploy" / "index.html"
    source.write_text(PLAINTEXT, encoding="utf-8")
    password_file.write_text("  " + PASSWORD + "\r\n", encoding="utf-8")   # the file's surrounding whitespace is trimmed
    assert LD.main(["--source", str(source), "--output", str(output), "--password-file", str(password_file)]) == 0
    return output.read_text(encoding="utf-8")


def decrypt(page, password):
    salt, iv, ct, iterations = re.search(r'var D=\{s:"([^"]+)",i:"([^"]+)",c:"([^"]+)",n:(\d+)\}', page).groups()
    key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), base64.b64decode(salt), int(iterations), dklen=32)
    return AESGCM(key).decrypt(base64.b64decode(iv), base64.b64decode(ct), None)


def test_password_field_is_masked_and_keeps_autocomplete_off(page):
    field = re.search(r"<input id=pw [^>]*>", page).group(0)
    assert "type=password" in field and "type=text" not in field and "autocomplete=off" in field
    assert page.count("<input") == 1


def test_error_text_no_longer_says_the_password_is_visible(page):
    assert "as you type" not in page and "you can see it" not in page
    assert "Wrong password" in page


def test_typed_password_is_still_trimmed_in_the_browser(page):
    assert "document.getElementById('pw').value.trim()" in page


def test_page_still_decrypts_to_the_source_bytes(page):
    assert decrypt(page, PASSWORD) == PLAINTEXT.encode("utf-8")


@pytest.mark.parametrize("wrong", [PASSWORD.upper(), PASSWORD + "x", "", " " + PASSWORD])
def test_wrong_password_fails(page, wrong):
    with pytest.raises(InvalidTag):
        decrypt(page, wrong)
