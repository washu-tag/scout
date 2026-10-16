import importlib.util
import json
import subprocess
import sys
from pathlib import Path
import unittest

import bcrypt


encoder_path = (
    Path(__file__).resolve().parents[2]
    / "roles"
    / "xnat"
    / "files"
    / "encode_admin_password.py"
)
spec = importlib.util.spec_from_file_location("xnat_password_encoder", encoder_path)
encoder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(encoder)


class XnatPasswordTests(unittest.TestCase):
    def assert_matches(self, password, encoded):
        self.assertTrue(encoded.startswith("{bcrypt}$2a$10$"))
        self.assertTrue(bcrypt.checkpw(password.encode(), encoded[8:].encode()))

    def test_ambiguous_hex_password_is_native_bcrypt(self):
        password = "a" * 64
        encoded = encoder.encode(password, "")
        self.assert_matches(password, encoded)
        self.assertFalse(bcrypt.checkpw(b"different-password", encoded[8:].encode()))

    def test_matching_hash_is_stable(self):
        encoded = encoder.encode("synthetic-password", "")
        self.assertEqual(encoder.encode("synthetic-password", encoded), encoded)

    def test_changed_password_replaces_previous_hash(self):
        previous = encoder.encode("synthetic-old-password", "")
        encoded = encoder.encode("synthetic-new-password", previous)
        self.assertNotEqual(previous, encoded)
        self.assert_matches("synthetic-new-password", encoded)

    def test_invalid_or_legacy_encoding_is_replaced(self):
        for previous in ("{bcrypt}malformed", "{bcrypt}é", "{sha256}legacy"):
            with self.subTest(previous=previous):
                encoded = encoder.encode("synthetic-password", previous)
                self.assertNotEqual(previous, encoded)
                self.assert_matches("synthetic-password", encoded)

    def test_existing_seed_encoding_is_reused(self):
        password = "a" * 64
        encoded = encoder.encode(password, "")
        result = subprocess.run(
            [sys.executable, str(encoder_path)],
            input=json.dumps(
                {
                    "password": password,
                    "previous_ini": "[system]\ndefaultAdminPassword=" + encoded + "\n",
                }
            ),
            text=True,
            capture_output=True,
            check=True,
        )
        self.assertEqual(result.stdout.strip(), encoded)

    def test_utf8_byte_limit(self):
        for password in ("x" * 72, "é" * 36):
            with self.subTest(length=len(password.encode())):
                self.assert_matches(password, encoder.encode(password, ""))
        for password in ("", "x" * 73, "é" * 37):
            with self.subTest(length=len(password.encode())):
                with self.assertRaises(ValueError):
                    encoder.encode(password, "")


if __name__ == "__main__":
    unittest.main()
