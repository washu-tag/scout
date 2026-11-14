"""Demo branches only: copy the tested candidate (fresh images, fresh charts and the
stamped scout-config) to GHCR under tags suffixed with the branch name, digests
unchanged. Nothing is signed and no main tag moves. Not for main."""

import importlib.util
import os
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location(
    "copy_flux", HERE / "copy-flux-artifacts.py"
)
copy_flux = importlib.util.module_from_spec(spec)
sys.path.insert(0, str(HERE))
spec.loader.exec_module(copy_flux)


def main():
    branch = os.environ["GITHUB_REF_NAME"]
    if not branch.startswith("demo-"):
        raise SystemExit("demo branches only")
    suffix = "-" + "".join(c if c.isalnum() or c in "._-" else "-" for c in branch)
    candidate = Path(sys.argv[1]).resolve()
    index = copy_flux.load(candidate)
    rows = [r for r in index["images"] + index["charts"] if r["fresh"]] + [
        index["config"]
    ]
    lines = ["| artifact | reference |", "|---|---|"]
    for row in rows:
        ref = copy_flux.copy({**row, "tag": row["tag"] + suffix}, candidate)
        lines.append(
            f"| {row.get('name', row['repository'])} | `{row['repository']}:{row['tag']}{suffix}` `{ref}` |"
        )
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    text = "\n".join(lines) + "\n"
    if summary:
        Path(summary).write_text(text)
    print(text)


if __name__ == "__main__":
    main()
