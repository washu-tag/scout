"""Exercise site key cleanup at early failure points without a registry or cluster."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest

SCRIPT = Path(__file__).resolve().with_name("publish_site.sh")


class SiteKeyCleanupTests(unittest.TestCase):
    def test_private_keys_removed_on_partial_generation_and_bootstrap_failure(self):
        for failure in ("second-key", "bootstrap"):
            with self.subTest(
                failure=failure
            ), tempfile.TemporaryDirectory() as directory:
                work = Path(directory)
                (work / "site").mkdir()
                (work / "site" / "kustomization.yaml").write_text("resources: []\n")
                (work / "cosign.pub").write_text("original config public key\n")
                binaries = work / "bin"
                binaries.mkdir()
                scripts = {
                    "cosign": """
                        from pathlib import Path
                        import os, sys
                        assert sys.argv[1] == "generate-key-pair"
                        prefix = Path(sys.argv[sys.argv.index("--output-key-prefix") + 1])
                        prefix.with_suffix(".key").write_text("ephemeral private key")
                        prefix.with_suffix(".pub").write_text("ephemeral public key")
                        if prefix.name == "wrong" and os.environ["FAIL_AT"] == "second-key":
                            raise SystemExit(17)
                    """,
                    "kubectl": """
                        import sys
                        if sys.argv[1] == "apply":
                            sys.stdin.read()
                            raise SystemExit(18)
                        print("kind: Secret")
                    """,
                    "oras": """
                        raise SystemExit("must not publish after key/bootstrap failure")
                    """,
                }
                for name, body in scripts.items():
                    command = binaries / name
                    command.write_text(f"#!{sys.executable}\n" + textwrap.dedent(body))
                    command.chmod(0o700)
                environment = dict(
                    os.environ,
                    PATH=str(binaries) + os.pathsep + os.environ["PATH"],
                    CI_REGISTRY="127.0.0.1:1",
                    VERSION="0.20261006.1",
                    RUNNER_TEMP=str(work),
                    TESTED_SHA="a" * 40,
                    GITHUB_REPOSITORY="washu-tag/scout",
                    GITHUB_ENV=str(work / "env"),
                    FAIL_AT=failure,
                )
                result = subprocess.run(
                    ["bash", str(SCRIPT)],
                    env=environment,
                    capture_output=True,
                    text=True,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertTrue((work / "site-trust" / "site.pub").is_file())
                self.assertFalse((work / "site-trust" / "site.key").exists())
                self.assertFalse((work / "site-trust" / "wrong.key").exists())
                self.assertEqual(
                    (work / "cosign.pub").read_text(), "original config public key\n"
                )
                self.assertFalse((work / "env").exists())
                self.assertEqual(
                    list((work / "site").iterdir()),
                    [work / "site" / "kustomization.yaml"],
                )


if __name__ == "__main__":
    unittest.main()
