FROM python:3.12-slim

# util-linux/openssl et al. lag the Debian security fix in this base image tag
# (same CVEs flagged on report-viewer's identical base); pull them in
# explicitly until upstream rebuilds. No-op once the base image catches up.
RUN apt-get update && apt-get install -y --no-install-recommends --only-upgrade \
        bsdutils libblkid1 liblastlog2-2 libmount1 libsmartcols1 libuuid1 login mount util-linux \
        libssl3t64 openssl openssl-provider-legacy \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HOME=/tmp

WORKDIR /app

COPY pyproject.toml /app/
COPY src /app/src
RUN pip install .

EXPOSE 8000

RUN groupadd --system --gid 1001 xnat-explore-poc \
    && useradd --system --uid 1001 --gid 1001 --home /app xnat-explore-poc
USER xnat-explore-poc

CMD ["python", "-m", "xnat_explore_poc"]
