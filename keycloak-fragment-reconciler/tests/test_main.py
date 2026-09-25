"""Startup wiring, and the one setting that is read before Settings exists.

`main` configures logging straight from the environment, so an unrecognised
level has to degrade to a usable one rather than raise out of a process that
has nowhere to report it yet.
"""

from __future__ import annotations

import logging

import pytest

from scout_keycloak_fragment_reconciler.main import resolve_log_level


class TestTheConfiguredLogLevel:
    @pytest.mark.parametrize(
        ("value", "level"),
        [
            ("INFO", logging.INFO),
            ("debug", logging.DEBUG),
            (" WARNING ", logging.WARNING),
            ("WARN", logging.WARNING),
            ("10", logging.DEBUG),
        ],
    )
    def test_a_level_the_logging_module_names_is_used(self, value, level):
        """`WARN` and `FATAL` are aliases the mapping carries, and a number is
        the other natural thing to set."""
        assert resolve_log_level(value) == (level, None)

    @pytest.mark.parametrize("value", ["Verbose", "TRACE", "", "10.0", "17"])
    def test_anything_else_is_info_and_reported(self, value):
        """A typo must not be a crash loop, and must not be silent either: the
        value comes back so the caller can name it once logging is up."""
        assert resolve_log_level(value) == (logging.INFO, value)

    def test_the_effective_level_is_one_logging_accepts(self):
        """The result is handed to basicConfig, which raises on anything it
        cannot read as a level."""
        for value in ["Verbose", "", "17", "debug"]:
            logging.getLogger("probe").setLevel(resolve_log_level(value)[0])
