"""JVM heap -> Kubernetes memory conversion for the Phase 3 config-artifact lane.

Byte-faithful stdlib port of ``ansible/filter_plugins/jvm_memory.py`` (the
``jvm_memory_to_k8s`` Jinja2 filter), so the GitOps ``gen_cluster_vars`` generator
and the Ansible tasks produce byte-identical memory strings. Algorithm/rounding/
unit selection copied verbatim; only the ``FilterModule`` wrapper and unused
``multiply_memory`` sibling are dropped. Parity proven by reusing the filter's own
vectors in ``test_jvm_memory.py``.

JVM heap units are binary despite notation (``-Xmx2G`` == 2 gibibytes), so they map
to K8s ``*i`` suffixes; a ``multiplier`` (2 for a limit) accounts for off-heap memory.
"""

import re


def jvm_memory_to_k8s(heap_size, multiplier=1):
    """
    Convert JVM heap size to Kubernetes memory resource specification.

    Args:
        heap_size: JVM heap size string (e.g., "2G", "2g", "512M", "1024k")
        multiplier: Optional multiplier for the memory (default: 1)
                   Use 2 for limits to account for off-heap memory

    Returns:
        Kubernetes memory string with proper binary suffix (e.g., "2Gi", "512Mi", "1024Ki")

    Examples:
        {{ cassandra_max_heap | jvm_memory_to_k8s }}         -> "2Gi"
        {{ cassandra_max_heap | jvm_memory_to_k8s(2) }}      -> "4Gi"
        {{ elasticsearch_max_heap | jvm_memory_to_k8s }}     -> "1Gi"
        {{ "512M" | jvm_memory_to_k8s }}                     -> "512Mi"
        {{ "512M" | jvm_memory_to_k8s(2) }}                  -> "1Gi" (1024Mi converted)
        {{ "1024K" | jvm_memory_to_k8s }}                    -> "1Mi" (converted up)
    """
    heap_str = str(heap_size).strip()

    # parse value + optional unit (case-insensitive)
    match = re.match(r"^(\d+(?:\.\d+)?)\s*([KMG])?$", heap_str, re.IGNORECASE)
    if not match:
        raise ValueError(
            f"Invalid heap size format: {heap_size}. "
            f"Expected format: '2G', '512M', '1024K' (case-insensitive)"
        )

    value_str, unit = match.groups()
    value = float(value_str)

    value *= multiplier

    # default unit is bytes if unspecified (rare but valid)
    if not unit:
        unit = "B"

    unit = unit.upper()

    # JVM units are binary despite lacking the 'i' suffix
    if unit == "K":
        value_bytes = value * 1024
    elif unit == "M":
        value_bytes = value * 1024 * 1024
    elif unit == "G":
        value_bytes = value * 1024 * 1024 * 1024
    else:  # B or unknown
        value_bytes = value

    # pick the largest divisible K8s unit (Gi > Mi > Ki); zero keeps its unit
    if value_bytes == 0:
        return f"0{unit}i"
    elif value_bytes >= 1024 * 1024 * 1024 and value_bytes % (1024 * 1024 * 1024) == 0:
        k8s_value = int(value_bytes / (1024 * 1024 * 1024))
        return f"{k8s_value}Gi"
    elif value_bytes >= 1024 * 1024 and value_bytes % (1024 * 1024) == 0:
        k8s_value = int(value_bytes / (1024 * 1024))
        return f"{k8s_value}Mi"
    elif value_bytes >= 1024 and value_bytes % 1024 == 0:
        k8s_value = int(value_bytes / 1024)
        return f"{k8s_value}Ki"
    else:  # fallback to bytes
        return f"{int(value_bytes)}"
