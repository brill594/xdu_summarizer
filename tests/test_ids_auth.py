import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from xdu_summarizer.xdu_downloader import authenticate_via_ids


class IDSAuthProtocolTests(unittest.TestCase):
    def _fake_helper(self, root: Path) -> Path:
        helper = root / "fake-ids-helper"
        helper.write_text(
            "#!/usr/bin/env python3\n"
            "import json, os, sys\n"
            "assert len(sys.argv) == 1\n"
            "assert 'XDU_IDS_PASSWORD' not in os.environ\n"
            "assert 'XDU_IDS_PROXY' not in os.environ\n"
            "request = json.loads(input())\n"
            "assert request['username'] == 'student'\n"
            "assert request['password'] == 'secret'\n"
            "print(json.dumps({'status': 'reauth_required', 'sent_to': 'masked'}), flush=True)\n"
            "code = json.loads(input())['code']\n"
            "if code == '000000':\n"
            "    print(json.dumps({'status': 'reauth_rejected', 'error': 'wrong code'}), flush=True)\n"
            "    code = json.loads(input())['code']\n"
            "assert code == '123456'\n"
            "print(json.dumps({'status': 'success', 'cookies': "
            "{'_d': 'd-value', 'UID': 'uid-value', 'vc3': 'vc3-value'}}), flush=True)\n",
            encoding="utf-8",
        )
        helper.chmod(0o700)
        return helper

    def test_credentials_use_stdin_and_second_factor_resumes_same_process(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            helper = self._fake_helper(Path(temp_dir))
            destinations = []

            with patch.dict(os.environ, {"XDU_IDS_PASSWORD": "must-not-leak", "XDU_IDS_PROXY": "proxy"}):
                result = authenticate_via_ids(
                    "student",
                    "secret",
                    helper_path=helper,
                    reauth_code_provider=lambda sent_to: destinations.append(sent_to) or "123456",
                )

            self.assertEqual(destinations, ["masked"])
            self.assertEqual(result["UID"], "uid-value")

    def test_rejected_second_factor_retries_in_same_process(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            helper = self._fake_helper(Path(temp_dir))
            prompts = []
            codes = iter(["000000", "123456"])

            result = authenticate_via_ids(
                "student",
                "secret",
                helper_path=helper,
                reauth_code_provider=lambda prompt: prompts.append(prompt) or next(codes),
            )

            self.assertEqual(prompts, ["masked", "wrong code"])
            self.assertEqual(result["vc3"], "vc3-value")

    def test_second_factor_fails_loud_without_code_provider(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            helper = self._fake_helper(Path(temp_dir))

            with self.assertRaisesRegex(RuntimeError, "no verification-code provider"):
                authenticate_via_ids("student", "secret", helper_path=helper)


if __name__ == "__main__":
    unittest.main()
