import io
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from xdu_summarizer.xdu_downloader import _download_and_extract


class DownloaderUpdateTests(unittest.TestCase):
    def _archive(self, *, subtitle_support: bool = True) -> bytes:
        buffer = io.BytesIO()
        downloader_source = "download_subtitle_for_row\nmerge_subtitles_for_videos\n" if subtitle_support else "old\n"
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("XDUClassVideoDownloader-main/XDUClassVideoDownloader.py", "new live downloader\n")
            archive.writestr("XDUClassVideoDownloader-main/Automation.py", "new automation\n")
            archive.writestr("XDUClassVideoDownloader-main/downloader.py", downloader_source)
        return buffer.getvalue()

    def test_refresh_preserves_authentication_and_automation_config(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "downloader"
            target.mkdir()
            (target / "XDUClassVideoDownloader.py").write_text("old\n", encoding="utf-8")
            (target / "Automation.py").write_text("old\n", encoding="utf-8")
            auth = target / "auth.ini"
            auth.write_text("secret auth state\n", encoding="utf-8")
            auth.chmod(0o600)
            (target / "automation_config.ini").write_text("course selection\n", encoding="utf-8")

            with patch("xdu_summarizer.xdu_downloader.urllib.request.urlopen", return_value=io.BytesIO(self._archive())):
                _download_and_extract(target)

            self.assertEqual(auth.read_text(encoding="utf-8"), "secret auth state\n")
            self.assertEqual((target / "automation_config.ini").read_text(encoding="utf-8"), "course selection\n")
            self.assertEqual((target / "XDUClassVideoDownloader.py").read_text(encoding="utf-8"), "new live downloader\n")
            if os.name == "posix":
                self.assertEqual(auth.stat().st_mode & 0o777, 0o600)

    def test_archive_without_subtitles_cannot_replace_working_downloader(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "downloader"
            target.mkdir()
            live_script = target / "XDUClassVideoDownloader.py"
            live_script.write_text("working old downloader\n", encoding="utf-8")
            (target / "Automation.py").write_text("working automation\n", encoding="utf-8")

            with patch(
                "xdu_summarizer.xdu_downloader.urllib.request.urlopen",
                return_value=io.BytesIO(self._archive(subtitle_support=False)),
            ):
                with self.assertRaisesRegex(RuntimeError, "subtitle support"):
                    _download_and_extract(target)

            self.assertEqual(live_script.read_text(encoding="utf-8"), "working old downloader\n")


if __name__ == "__main__":
    unittest.main()
