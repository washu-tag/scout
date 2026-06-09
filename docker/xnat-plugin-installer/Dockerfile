# Source-agnostic XNAT plugin installer init container.
#
# Base: Maven 3.9 on Temurin JDK 21 — Maven resolves plugin coordinates
# (Pattern D) and the bundled JDK 21 `jar` tool repackages the jar after the
# logback rewrite (JDK 21 matches the XNAT 1.10 runtime). curl handles URL
# downloads; python3 runs the logback rewrite.
FROM maven:3.9-eclipse-temurin-21

RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 curl \
 && rm -rf /var/lib/apt/lists/*

COPY install-plugin.sh /usr/local/bin/install-plugin.sh
COPY rewrite-logback.py /usr/local/bin/rewrite-logback.py
RUN chmod +x /usr/local/bin/install-plugin.sh /usr/local/bin/rewrite-logback.py

ENTRYPOINT ["/usr/local/bin/install-plugin.sh"]
