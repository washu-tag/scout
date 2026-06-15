# Source-agnostic XNAT plugin installer init container.
#
# Base: Maven 3.9 on Temurin JDK 21 — Maven resolves plugin coordinates
# and the bundled JDK 21 `jar` tool repackages the jar after the
# logback rewrite (JDK 21 matches the XNAT 1.10 runtime). curl handles URL
# downloads; python3 runs the logback rewrite.
FROM maven:3.9-eclipse-temurin-21

# python3-defusedxml (Debian/Ubuntu ships 0.7.1, the current release) backs the
# hardened XML parsing in rewrite-logback.py — preferred over pip to avoid a
# PEP 668 externally-managed-environment break on the base image.
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-defusedxml curl \
 && rm -rf /var/lib/apt/lists/*

COPY install-plugin.sh /usr/local/bin/install-plugin.sh
COPY rewrite-logback.py /usr/local/bin/rewrite-logback.py
RUN chmod +x /usr/local/bin/install-plugin.sh /usr/local/bin/rewrite-logback.py

# Run as a non-root user. UID 1000 matches the xnat-web image's `tomcat` user, so
# JARs written to the shared home-plugins volume are owned consistently with the
# main container. The k8s pod also pins runAsUser=1000 + fsGroup=1000 (so the
# emptyDir is group-writable); this USER makes non-root the image default too.
# install-plugin.sh keeps all writes (Maven local repo, truststore copy) under a
# /tmp workdir, so no root-owned path is touched at runtime. HOME is set so tools
# that probe it don't fall back to an unwritable "/".
ENV HOME=/tmp
USER 1000
ENTRYPOINT ["/usr/local/bin/install-plugin.sh"]
