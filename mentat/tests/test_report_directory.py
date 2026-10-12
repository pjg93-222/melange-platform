import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from report_directory import report_directory, require_report_path


class ReportDirectoryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.reports=self.root/'authoritative';self.reports.mkdir()
        (self.root/'config').mkdir()
        (self.root/'config'/'report-directory.json').write_text(json.dumps({
            'schema_version':1,'directory':str(self.reports)}))
        self.env=patch.dict(os.environ, {'MENTAT_REPORT_DIRECTORY':''})
        self.env.start();self.addCleanup(self.env.stop)

    def test_configuration_selects_exact_directory(self):
        self.assertEqual(report_directory(self.root),self.reports.resolve())

    def test_explicit_environment_supports_isolated_runtime(self):
        other=self.root/'other';other.mkdir()
        with patch.dict(os.environ,{'MENTAT_REPORT_DIRECTORY':str(other)}):
            self.assertEqual(report_directory(self.root/'no-config'),other.resolve())

    def test_missing_folder_never_falls_back_or_creates_it(self):
        missing=self.root/'missing'
        with patch.dict(os.environ,{'MENTAT_REPORT_DIRECTORY':str(missing)}):
            with self.assertRaises(ValueError):report_directory(self.root)
        self.assertFalse(missing.exists())

    def test_relative_configuration_rejected(self):
        with patch.dict(os.environ,{'MENTAT_REPORT_DIRECTORY':'reports'}):
            with self.assertRaises(ValueError):report_directory(self.root)

    def test_old_receipt_path_outside_folder_rejected(self):
        with self.assertRaises(ValueError):require_report_path(self.root/'stale.xlsx',self.reports)
        self.assertEqual(require_report_path(self.reports/'Banking.xlsx',self.reports),self.reports/'Banking.xlsx')

    def test_parent_traversal_rejected(self):
        with self.assertRaises(ValueError):require_report_path(self.reports/'..'/'stale.xlsx',self.reports)


if __name__=='__main__':unittest.main()
