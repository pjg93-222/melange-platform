# -*- coding: utf-8 -*-
"""Privacy and record-shape invariants for public-safe staff artifacts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import re
from typing import Any


PROHIBITED_PUBLIC_KEYS = {
    "email",
    "email_address",
    "phone",
    "phone_number",
    "fax",
    "address",
    "street_address",
    "contact_hash",
    "contact_email_hash",
    "contact_phone_hash",
}


class PublicSafetyError(ValueError):
    pass


_EMAIL_VALUE = re.compile(r"(?<![\w.+-])[\w.+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?![\w-]|\.[A-Za-z0-9])")
_PHONE_VALUE = re.compile(
    r"(?<!\d)(?:\+1[ .-])?(?:\(\d{3}\)[ .-]?|\d{3}[ .-])\d{3}[ .-]\d{4}(?!\d)"
)


def find_prohibited_fields(value: Any, path: str = "$") -> list[str]:
    findings: list[str] = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            key_text = str(key)
            child_path = f"{path}.{key_text}"
            if key_text.casefold() in PROHIBITED_PUBLIC_KEYS:
                findings.append(child_path)
            findings.extend(find_prohibited_fields(child, child_path))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, child in enumerate(value):
            findings.extend(find_prohibited_fields(child, f"{path}[{index}]"))
    return findings


def find_prohibited_values(value: Any, path: str = "$") -> list[str]:
    """Find obvious contact values even when hidden under an innocuous key."""

    findings: list[str] = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            findings.extend(find_prohibited_values(child, f"{path}.{key}"))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, child in enumerate(value):
            findings.extend(find_prohibited_values(child, f"{path}[{index}]"))
    elif isinstance(value, str) and (
        _EMAIL_VALUE.search(value) is not None or _PHONE_VALUE.search(value) is not None
    ):
        findings.append(path)
    return findings


def assert_public_safe(value: Any) -> None:
    findings = find_prohibited_fields(value) + find_prohibited_values(value)
    if findings:
        raise PublicSafetyError("prohibited public fields/values: " + ", ".join(findings[:20]))


def inspect_legacy_directory(document: Mapping[str, Any]) -> dict[str, int]:
    """Measure the known compact Quorum legacy shape without exporting values."""

    rows: list[Mapping[str, Any]] = []
    for office_rows in document.values():
        if isinstance(office_rows, list):
            rows.extend(row for row in office_rows if isinstance(row, Mapping))
    return {
        "offices": len(document),
        "rows": len(rows),
        "email_values": sum(bool(str(row.get("e") or "").strip()) for row in rows),
        "phone_values": sum(bool(str(row.get("p") or "").strip()) for row in rows),
    }
