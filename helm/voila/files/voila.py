"""Voila config for Scout customizations.

Loaded automatically by Voila from /etc/jupyter/voila.py. Voila's
JupyterApp initialization calls `load_config_file("voila", ...)`, which
expects the filename pattern `voila.py` (basefilename + ".py") — not
`voila_config.py` despite that being the typical Jupyter convention.

Importing voila_runtime triggers the TornadoVoilaHandler monkey-patch at
module-load time, so the import here is load-bearing — don't remove it
as "unused." voila_runtime is a sibling file in this ConfigMap, mounted
at /opt/scout/ and placed on PYTHONPATH. The kernel manager subclass is
registered via Voila's own hook (NOT `c.ServerApp.kernel_manager_class`:
Voila instantiates its kernel manager from
`VoilaConfiguration.multi_kernel_manager_class`, not ServerApp's setting).
"""

import voila_runtime  # noqa: F401 — monkey-patches TornadoVoilaHandler on import

c.VoilaConfiguration.multi_kernel_manager_class = (
    "voila_runtime.ScoutMappingKernelManager"  # noqa: F821
)
