"""assert_output_path must compare whole path components, not string prefixes."""
import os

import pytest

from fec.collect import campaign_sites_2026 as campaign_sites


def test_paths_inside_the_out_dir_are_accepted(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    campaign_sites.assert_output_path(out, out)
    campaign_sites.assert_output_path(out, out / "raw" / "a.json")
    campaign_sites.assert_output_path(str(out), os.path.join(str(out), "..", "out", "b.json"))


def test_a_sibling_sharing_the_prefix_is_rejected(tmp_path):
    out = tmp_path / "out"
    (tmp_path / "out2").mkdir()
    out.mkdir()
    for escaped in (tmp_path / "out2", tmp_path / "out2" / "x.json", tmp_path / "out-backup" / "x.json", tmp_path / "outside"):
        with pytest.raises(ValueError, match="escapes out-dir"):
            campaign_sites.assert_output_path(out, escaped)


def test_traversal_and_the_parent_are_rejected(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    for escaped in (tmp_path, out / ".." / "elsewhere.json", out / ".." / "out2" / "x.json"):
        with pytest.raises(ValueError):
            campaign_sites.assert_output_path(out, escaped)


@pytest.mark.skipif(os.name != "nt", reason="Windows paths are case-insensitive and can sit on another drive")
def test_windows_case_difference_is_accepted_and_another_drive_is_rejected(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    campaign_sites.assert_output_path(str(out).upper(), str(out).lower() + "\\child.json")
    other = "D:\\" if not str(tmp_path).upper().startswith("D:") else "C:\\"
    with pytest.raises(ValueError):
        campaign_sites.assert_output_path(out, other + "elsewhere.json")
