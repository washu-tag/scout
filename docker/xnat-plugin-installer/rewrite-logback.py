#!/usr/bin/env python3
"""Rewrite an XNAT plugin's bundled logback config to log to stdout.

XNAT plugins ship a logback config that writes to a (rolling) file under
``${xnat.home}/logs``. In Kubernetes we want logs on stdout so they land in the
pod log stream (and Loki). This rewrites every ``<appender>`` to a
``ConsoleAppender``, drops the file/rolling-policy children (keeping only the
encoder), and prefixes the encoder pattern with the appender name so lines from
different appenders remain distinguishable on the shared stream.

Ported from the logback-edit step XnatWorks runs when building per-plugin
images; kept deliberately faithful so behavior matches those images.
"""
import argparse
import os
import xml.etree.ElementTree as ET


def edit(logfile):
    tree = ET.parse(logfile)
    root = tree.getroot()
    for appender in root.findall("appender"):
        name = appender.get("name")
        appender.set("class", "ch.qos.logback.core.ConsoleAppender")
        remove_items = []
        for item in appender:
            if item.tag.lower() != "encoder":
                remove_items.append(item)
            else:
                pattern = item.find("pattern")
                if pattern is not None and pattern.text:
                    pattern.text = pattern.text.replace("%d ", "%d [" + name + "] ")
        for elem in remove_items:
            appender.remove(elem)
    tree.write(logfile, "UTF-8", True)


def main():
    parser = argparse.ArgumentParser(
        description="Rewrite a logback.xml to log to stdout"
    )
    parser.add_argument(
        "--logfile", required=True, help="path to the logback xml to rewrite"
    )
    args = parser.parse_args()
    if not os.path.isfile(args.logfile):
        raise SystemExit("logback file not found: " + args.logfile)
    edit(args.logfile)


if __name__ == "__main__":
    main()
