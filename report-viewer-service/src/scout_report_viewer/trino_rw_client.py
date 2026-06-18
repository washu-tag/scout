"""Removed in V1.1 — see ADR 0026 "Just-in-time search evaluation".

Searches are now saved SQL only; nothing about which rows match is
stored, so there's no Delta search_members table to write to and
nothing requires the trino-rw instance. This file is kept as a
breadcrumb so an import-grep finds the removal note; delete it after
the V1.1 changes settle.
"""
