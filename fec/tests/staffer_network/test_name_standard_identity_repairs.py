"""Bounded identity repairs, including names the header rule must preserve."""
import pytest

from fec.staffer_network.name_standard import parse_name, name_key


@pytest.mark.parametrize("raw,last,first", [
    ("DE LOS REYES VANESSA M.", "DE LOS REYES", "VANESSA"),
    ("DE LAS CRUZ MARIA V.", "DE LAS CRUZ", "MARIA"),
    ("DE LA O IRVIN F", "DE LA O", "IRVIN"),
    ("LE CATHERINE L.", "LE", "CATHERINE"),
    ("VAN BRITTANY M", "VAN", "BRITTANY"),
])
def test_particle_boundaries(raw, last, first):
    parts = parse_name(raw, "house_csv")
    assert parts.last.upper() == last
    assert parts.first.upper() == first


@pytest.mark.parametrize("header", ["SENATE STEERING COMMITTEE", "OF THE SENATE"])
def test_header_is_bounded_and_raw_is_preserved(header):
    raw = header + "BAIG, WENDY F"
    parts = parse_name(raw, "payroll_comma")
    assert parts.last.upper() == "BAIG"
    assert parts.raw == raw
    assert name_key(parts) == name_key(parse_name("BAIG, WENDY F", "payroll_comma"))
    assert parse_name("BAIG, WENDY " + header, "payroll_comma").last.upper() == "BAIG"
    assert parse_name(header + ", WENDY", "payroll_comma").last.upper() == header


def test_display_names_are_not_header_stripped():
    assert parse_name("SENATE STEERING COMMITTEEBAIG", "display").last.upper() == "COMMITTEEBAIG"
