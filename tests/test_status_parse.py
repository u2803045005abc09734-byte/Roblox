import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from session import parse_status  # noqa: E402


class ParseStatusTests(unittest.TestCase):
    def test_logged_in(self):
        self.assertEqual(parse_status('{"id": 42, "name": "Foo", "displayName": "F"}'), ("verified", 42, "Foo"))

    def test_logged_out(self):
        body = '{"errors":[{"code":0,"message":"Authorization has been denied for this request."}]}'
        self.assertEqual(parse_status(body), ("loggedout", 0, ""))

    def test_unknown_for_garbage_or_network_errors(self):
        for text in ("", "   ", "<html>Error</html>", "null", "[]", '{"id": 0, "name": "x"}', '{"id": true, "name": "x"}', '{"name": "x"}'):
            self.assertEqual(parse_status(text)[0], "unknown", text)

    def test_none_input(self):
        self.assertEqual(parse_status(None)[0], "unknown")


if __name__ == "__main__":
    unittest.main()
