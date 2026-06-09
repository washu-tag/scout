#!/usr/bin/env bash
#
# Source-agnostic XNAT plugin installer, run as an init container.
#
# Acquires a plugin JAR from one of three sources, rewrites its bundled logback
# config to log to stdout (so plugin logs land in the pod log stream), and
# copies it into the shared `home-plugins` volume the XNAT container reads.
#
# Driven entirely by environment variables:
#   PLUGIN_NAME                  label for log lines (required)
#   PLUGIN_TARGET                output filename in DEST_DIR, e.g. openid-auth-plugin.jar (required)
#   PLUGIN_SOURCE_TYPE           file | url | coordinates (required)
#   PLUGIN_FILE                  source jar path (required for type=file)
#   PLUGIN_URL                   download URL (required for type=url)
#   PLUGIN_COORDINATES           group:artifact:version:classifier:type (required for type=coordinates)
#   PLUGIN_REPO_URL              Maven repo to resolve coordinates from (required for type=coordinates)
#   PLUGIN_SKIP_LOGBACK_REWRITE  "true" to skip the stdout rewrite (default "false")
#   DEST_DIR                     plugins dir (default /data/xnat/home/plugins)
#
# Image-baked plugins (the chart's native `plugins:` map) do NOT use this
# installer — they are pre-built and already log to stdout.
set -euo pipefail

: "${PLUGIN_NAME:?PLUGIN_NAME required}"
: "${PLUGIN_TARGET:?PLUGIN_TARGET required}"
: "${PLUGIN_SOURCE_TYPE:?PLUGIN_SOURCE_TYPE required}"
DEST_DIR="${DEST_DIR:-/data/xnat/home/plugins}"
SKIP_REWRITE="${PLUGIN_SKIP_LOGBACK_REWRITE:-false}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REWRITE_LOGBACK="$SCRIPT_DIR/rewrite-logback.py"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
JAR="$WORK/$PLUGIN_TARGET"

log() { echo "[install-plugin:$PLUGIN_NAME] $*"; }

# Rewrite the plugin's bundled logback config to a ConsoleAppender (stdout).
# No-ops cleanly when the plugin has no *-plugin.properties or no
# logConfiguration entry, so it is safe to run on every raw jar.
rewrite_logback() {
  local jar="$1" props logpath exdir builddir

  props="$(jar tf "$jar" | grep -E 'META-INF/xnat/.*-plugin\.properties$' | head -1 || true)"
  if [ -z "$props" ]; then
    log "no *-plugin.properties in jar; skipping logback rewrite"
    return 0
  fi

  exdir="$WORK/props"
  mkdir -p "$exdir"
  ( cd "$exdir" && jar xf "$jar" "$props" )

  logpath="$(grep -E '^logConfiguration=' "$exdir/$props" | head -1 | cut -d= -f2- | tr -d '\r')"
  if [ -z "$logpath" ]; then
    log "no logConfiguration in $props; skipping logback rewrite"
    return 0
  fi
  logpath="${logpath#/}"   # make archive-relative if absolute

  builddir="$WORK/build"
  mkdir -p "$builddir"
  ( cd "$builddir" && jar xf "$jar" )
  if [ ! -f "$builddir/$logpath" ]; then
    log "WARNING: $logpath declared but not present in jar; skipping rewrite"
    return 0
  fi

  log "rewriting logback config to stdout: $logpath"
  python3 "$REWRITE_LOGBACK" --logfile "$builddir/$logpath"

  rm -f "$jar"
  ( cd "$builddir" && jar cf "$jar" . )
}

log "source=$PLUGIN_SOURCE_TYPE target=$PLUGIN_TARGET dest=$DEST_DIR"

case "$PLUGIN_SOURCE_TYPE" in
  file)
    : "${PLUGIN_FILE:?PLUGIN_FILE required for source type 'file'}"
    cp "$PLUGIN_FILE" "$JAR"
    ;;
  url)
    : "${PLUGIN_URL:?PLUGIN_URL required for source type 'url'}"
    curl -fsSL -o "$JAR" "$PLUGIN_URL"
    ;;
  coordinates)
    : "${PLUGIN_COORDINATES:?PLUGIN_COORDINATES required for source type 'coordinates'}"
    : "${PLUGIN_REPO_URL:?PLUGIN_REPO_URL required for source type 'coordinates'}"
    mvn -q -B org.apache.maven.plugins:maven-dependency-plugin:3.6.1:copy \
      -Dartifact="$PLUGIN_COORDINATES" \
      -DremoteRepositories="$PLUGIN_REPO_URL" \
      -Dmdep.useBaseVersion=true \
      -DoutputDirectory="$WORK/dl"
    resolved="$(find "$WORK/dl" -maxdepth 1 -type f -name '*.jar' | head -1)"
    [ -n "$resolved" ] || { log "ERROR: maven copy produced no jar"; exit 1; }
    mv "$resolved" "$JAR"
    ;;
  *)
    log "ERROR: unknown PLUGIN_SOURCE_TYPE '$PLUGIN_SOURCE_TYPE' (want file|url|coordinates)"
    exit 1
    ;;
esac

if [ "$SKIP_REWRITE" = "true" ]; then
  log "logback rewrite skipped (PLUGIN_SKIP_LOGBACK_REWRITE=true)"
else
  rewrite_logback "$JAR"
fi

mkdir -p "$DEST_DIR"
cp "$JAR" "$DEST_DIR/$PLUGIN_TARGET"
log "installed $PLUGIN_TARGET:"
ls -l "$DEST_DIR"
