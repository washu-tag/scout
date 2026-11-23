"""Writes the build context repo2docker would hand to Docker, pointed at the site's mirrors.

repo2docker only builds through a Docker daemon, which the runners do not have, so
this renders the context and BuildKit builds it. Its own downloads of micromamba and
the base Python environment are hard-coded to anaconda.org and get rewritten to the
conda mirror.
"""

import os
import sys
import tarfile
from pathlib import Path

from repo2docker.app import Repo2Docker

src, out = sys.argv[1], Path(sys.argv[2])
ca = Path("/tools/ca.crt")
conda = os.environ.get("CONDA_CHANNEL_ALIAS", "").rstrip("/")
apt = os.environ.get("APT_MIRROR", "").rstrip("/")
apt_security = os.environ.get("APT_SECURITY_MIRROR", "").rstrip("/")
pip = os.environ.get("PIP_INDEX_URL", "")
bundle = "/etc/ssl/certs/ca-certificates.crt"


class Capture:
    def build(self, *, fileobj, **kwargs):
        with tarfile.open(fileobj=fileobj) as tar:
            tar.extractall(out, filter="tar")
        yield from ()


app = Repo2Docker()
os.chdir(src)
buildpacks = [bp(base_image=app.base_image) for bp in app.buildpacks]
buildpack = next((bp for bp in buildpacks if bp.detect()), None)
if buildpack is None:
    buildpack = app.default_buildpack(base_image=app.base_image)
buildpack.platform = "linux/amd64"
print(f"repo2docker {app.version}: {type(buildpack).__name__}")
build_args = {"NB_USER": app.user_name, "NB_UID": "1000"}
for _ in buildpack.build(Capture(), "", 0, build_args, [], {}):
    pass

if conda:
    for path in [out / "Dockerfile", *(out / "build_script_files").rglob("*")]:
        if path.is_file():
            text = path.read_text()
            text = text.replace(
                "https://anaconda.org/conda-forge/micromamba/${MAMBA_VERSION}/download/${CONDA_PLATFORM}/",
                f"{conda}/conda-forge/${{CONDA_PLATFORM}}/",
            ).replace("https://conda.anaconda.org/", f"{conda}/")
            path.write_text(text)

setup = []
if ca.exists():
    (out / "scout-ca.crt").write_bytes(ca.read_bytes())
    setup += [
        "COPY scout-ca.crt /usr/local/share/ca-certificates/scout-ca.crt",
        "RUN update-ca-certificates",
        f"ENV PIP_CERT={bundle} SSL_CERT_FILE={bundle}",
    ]
if apt:
    setup.append(
        "RUN sed -i"
        f" -e 's#http://archive.ubuntu.com/ubuntu/#{apt}/#'"
        f" -e 's#http://security.ubuntu.com/ubuntu/#{apt_security or apt}/#'"
        " /etc/apt/sources.list.d/ubuntu.sources"
    )
if conda:
    condarc = f"channel_alias: {conda}\\n" + (
        f"ssl_verify: {bundle}\\n" if ca.exists() else ""
    )
    setup.append(f"RUN mkdir -p /etc/conda && printf '{condarc}' > /etc/conda/.condarc")
if pip:
    setup.append(f"ENV PIP_INDEX_URL={pip}")

dockerfile = (out / "Dockerfile").read_text().splitlines()
first = next(i for i, line in enumerate(dockerfile) if line.startswith("FROM "))
(out / "Dockerfile").write_text(
    "\n".join(dockerfile[: first + 1] + setup + dockerfile[first + 1 :]) + "\n"
)
