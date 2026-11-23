"""Build the XNAT plugin jar that loads the launchpad frame-location script (ADR 0042).

The jar holds one header template, which XNAT includes in every page's <head>. It
is the same for every site: the script finds the launchpad one label above XNAT's
own host, so Ansible and GitOps deployments install the same bytes.

Usage: xnat_frame_location_jar.py OUTPUT.jar
"""

import sys
import zipfile
from pathlib import Path

SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "deploy/base/launchpad-frame-location/frame-location.js"
)
PLACEHOLDER = "'https://${server_hostname}'"
LAUNCHPAD = (
    "window.location.protocol + '//' + window.location.host.replace(/^[^.]+\\./, '')"
)
ENTRY = "META-INF/resources/templates/screens/header/scout-frame-location.vm"
# A form's result page sits at the action URL, which must not become a link.
TEMPLATE = "#if($data.request.method == 'GET')\n<script>\n{script}</script>\n#end\n"


def template() -> str:
    script = SCRIPT.read_text()
    if script.count(PLACEHOLDER) != 1:
        raise SystemExit(f"{SCRIPT}: expected one {PLACEHOLDER}")
    return TEMPLATE.format(script=script.replace(PLACEHOLDER, LAUNCHPAD))


def build(output: Path) -> None:
    # Fixed timestamps, so the same template always gives the same jar bytes.
    def entry(name: str, mode: int) -> zipfile.ZipInfo:
        info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
        info.external_attr = mode << 16
        return info

    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as jar:
        # XNAT finds templates by classpath wildcard, which needs the directory entries.
        parts = ENTRY.split("/")[:-1]
        for depth in range(1, len(parts) + 1):
            jar.writestr(entry("/".join(parts[:depth]) + "/", 0o40755), "")
        jar.writestr(entry(ENTRY, 0o100644), template())


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    build(Path(sys.argv[1]))
