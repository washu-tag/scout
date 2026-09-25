"""Tests for check_version_copies: the repository's copies agree, and the checker reads
renovate.json5's JSON5, follows depNameTemplate managers, and reports a hand-edited copy.
"""

import json
from pathlib import Path

from check_version_copies import collect, find_drift, strip_json5

REPO = Path(__file__).resolve().parents[2]

CONFIG = """{
  // two managers, like renovate.json5's
  "customManagers": [
    {
      "customType": "regex",
      "managerFilePatterns": ["versions.yaml", "deploy/**/*.yaml"],
      "matchStrings": [
        "#\\\\s*renovate:\\\\s*datasource=(?<datasource>[^\\\\s]+)\\\\s+depName=(?<depName>[^\\\\s]+?)\\\\n\\\\s*[a-z_]*(?:tag|version):\\\\s*'?(?<currentValue>[^\\\\s']+)",
      ],
    },
    {
      "customType": "regex",
      "managerFilePatterns": ["app/VERSION"],
      "matchStrings": ["^(?<currentValue>\\\\d[\\\\d\\\\.]*)\\\\s*$"], /* whole-file pin */
      "depNameTemplate": "org/app",
    },
  ],
}
"""


def _repo(tmp_path: Path, deploy_tag: str) -> Path:
    (tmp_path / "renovate.json5").write_text(CONFIG)
    (tmp_path / "versions.yaml").write_text(
        "# renovate: datasource=docker depName=org/app\napp_image_tag: 1.2.3\n"
        "# renovate: datasource=docker depName=org/db\ndb_image_tag: 16.1\n"
    )
    (tmp_path / "deploy/base").mkdir(parents=True)
    (tmp_path / "deploy/base/app.yaml").write_text(
        f"image:\n  # renovate: datasource=docker depName=org/app\n  tag: '{deploy_tag}'\n"
    )
    (tmp_path / "app").mkdir()
    (tmp_path / "app/VERSION").write_text("1.2.3\n")
    return tmp_path


def test_repository_copies_agree():
    drift = find_drift(collect(REPO))
    assert drift == {}, "copies of a pinned version differ; see check_version_copies.py"


def test_strip_json5_keeps_strings_and_drops_comments_and_trailing_commas():
    text = '{\n  // note\n  "url": "https://x//y/*z*/", /* block */\n  "list": [1, 2,],\n}\n'
    assert json.loads(strip_json5(text)) == {"url": "https://x//y/*z*/", "list": [1, 2]}


def test_consistent_copies_including_template_manager(tmp_path):
    copies = collect(_repo(tmp_path, "1.2.3"))
    assert sorted(copies["org/app"]) == [
        ("app/VERSION", "1.2.3"),
        ("deploy/base/app.yaml", "1.2.3"),
        ("versions.yaml", "1.2.3"),
    ]
    assert find_drift(copies) == {}


def test_hand_edited_copy_is_reported(tmp_path):
    drift = find_drift(collect(_repo(tmp_path, "1.2.4")))
    assert list(drift) == ["org/app"]
    assert ("deploy/base/app.yaml", "1.2.4") in drift["org/app"]
