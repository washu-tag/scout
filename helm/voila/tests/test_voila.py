"""Unit test for voila.py - the Voila config that wires Scout's customizations.

voila.py can't be imported normally: it runs under a magic `c` global that
Voila injects at load time. We exec it with a stand-in `c` to (a) prove it
executes (the load-bearing `import voila_runtime` side-effect and the
assignments) and (b) prove the dotted class paths it registers actually resolve
-- a rename of ScoutMappingKernelManager that didn't update voila.py would
otherwise silently disable identity injection (Voila falls back to its default
kernel manager), and likewise for the kernel websocket filter.
"""

import importlib
import pathlib
from types import SimpleNamespace

import voila_runtime


def _registered_class(section, trait):
    voila_py = pathlib.Path(voila_runtime.__file__).with_name("voila.py")
    captured = SimpleNamespace(
        VoilaConfiguration=SimpleNamespace(), Voila=SimpleNamespace()
    )

    exec(compile(voila_py.read_text(), str(voila_py), "exec"), {"c": captured})

    dotted = getattr(getattr(captured, section), trait)
    module_name, _, class_name = dotted.rpartition(".")
    return getattr(importlib.import_module(module_name), class_name)


def test_voila_config_registers_resolvable_kernel_manager():
    resolved = _registered_class("VoilaConfiguration", "multi_kernel_manager_class")
    assert resolved is voila_runtime.ScoutMappingKernelManager


def test_voila_config_registers_resolvable_websocket_connection():
    resolved = _registered_class("Voila", "kernel_websocket_connection_class")
    assert resolved is voila_runtime.ScoutKernelWebsocketConnection
