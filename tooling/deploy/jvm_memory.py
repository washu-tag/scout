"""JVM heap -> Kubernetes memory conversion for the Phase 3 config-artifact lane.

Byte-faithful stdlib port of ``ansible/filter_plugins/jvm_memory.py`` (the
``jvm_memory_to_k8s`` Jinja2 filter). The GitOps ``gen_cluster_vars`` generator
computes the same cassandra/elasticsearch/hl7-transformer memory
requests+limits the Ansible tasks compute, so the two lanes must produce
byte-identical strings. The algorithm, rounding, and unit selection are copied
verbatim from the filter; only the Ansible ``FilterModule`` wrapper (and the
unused ``multiply_memory`` sibling) are dropped. Parity is proven by reusing the
filter's own unit-test vectors in ``test_jvm_memory.py``.

JVM heap units are binary despite their notation (``-Xmx2G`` == 2 gibibytes), so
they map to K8s ``*i`` suffixes (``2Gi``); a ``multiplier`` (2 for a limit vs a
request, etc.) accounts for off-heap memory.
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
    # Convert to string and strip whitespace
    heap_str = str(heap_size).strip()

    # Parse the value and unit (case-insensitive)
    match = re.match(r"^(\d+(?:\.\d+)?)\s*([KMG])?$", heap_str, re.IGNORECASE)
    if not match:
        raise ValueError(
            f"Invalid heap size format: {heap_size}. "
            f"Expected format: '2G', '512M', '1024K' (case-insensitive)"
        )

    value_str, unit = match.groups()
    value = float(value_str)

    # Apply multiplier
    value *= multiplier

    # Default unit is bytes if not specified (rare but valid)
    if not unit:
        unit = "B"

    # Convert to uppercase for consistency
    unit = unit.upper()

    # Convert to base value in bytes, then to appropriate K8s unit
    # JVM units are all binary (despite lacking 'i' suffix)
    if unit == "K":
        # Convert Ki to appropriate unit
        value_bytes = value * 1024
    elif unit == "M":
        # Convert Mi to appropriate unit
        value_bytes = value * 1024 * 1024
    elif unit == "G":
        # Convert Gi to appropriate unit
        value_bytes = value * 1024 * 1024 * 1024
    else:  # B or unknown
        value_bytes = value

    # Determine best K8s unit (prefer Gi > Mi > Ki)
    # Handle zero specially - use the original unit
    if value_bytes == 0:
        return f"0{unit}i"
    # Use Gi if divisible by 1Gi
    elif value_bytes >= 1024 * 1024 * 1024 and value_bytes % (1024 * 1024 * 1024) == 0:
        k8s_value = int(value_bytes / (1024 * 1024 * 1024))
        return f"{k8s_value}Gi"
    # Use Mi if divisible by 1Mi
    elif value_bytes >= 1024 * 1024 and value_bytes % (1024 * 1024) == 0:
        k8s_value = int(value_bytes / (1024 * 1024))
        return f"{k8s_value}Mi"
    # Use Ki if divisible by 1Ki
    elif value_bytes >= 1024 and value_bytes % 1024 == 0:
        k8s_value = int(value_bytes / 1024)
        return f"{k8s_value}Ki"
    # Fallback to bytes
    else:
        return f"{int(value_bytes)}"
