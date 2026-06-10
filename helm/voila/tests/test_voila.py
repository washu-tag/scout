"""Unit test for voila.py - the Voila config that wires Scout's customizations.

voila.py can't be imported normally: it runs under a magic `c` global that
Voila injects at load time. We exec it with a stand-in `c` to (a) prove it
executes (the load-bearing `import voila_runtime` side-effect and the
assignment) and (b) prove the dotted class path it registers actually resolves
-- a rename of ScoutMappingKernelManager that didn't update voila.py would
otherwise silently disable identity injection (Voila falls back to its default
kernel manager).
"""

import importlib
import pathlib
from types import SimpleNamespace

import voila_runtime


def test_voila_config_registers_resolvable_kernel_manager():
    voila_py = pathlib.Path(voila_runtime.__file__).with_name("voila.py")
    captured = SimpleNamespace(VoilaConfiguration=SimpleNamespace())

    exec(compile(voila_py.read_text(), str(voila_py), "exec"), {"c": captured})

    dotted = captured.VoilaConfiguration.multi_kernel_manager_class
    module_name, _, class_name = dotted.rpartition(".")
    resolved = getattr(importlib.import_module(module_name), class_name)
    assert resolved is voila_runtime.ScoutMappingKernelManager
