#!/usr/bin/env python3
"""
Custom Jinja2 filters for JVM memory conversions.

These filters convert JVM heap sizes (which use binary units despite notation)
to Kubernetes memory resource specifications (which require 'i' suffix for binary units).

Background:
- JVM: -Xmx2G or -Xmx2g = 2 gibibytes (2 * 1024^3 bytes) - binary
- K8s: 2G = 2 gigabytes (2 * 1000^3 bytes) - decimal
- K8s: 2Gi = 2 gibibytes (2 * 1024^3 bytes) - binary
- To ensure JVM and K8s use same binary units: JVM 'G' -> K8s 'Gi'

Supported JVM units (all interpreted as binary by JVM):
- K/k = kibibytes (1024 bytes)
- M/m = mebibytes (1024^2 bytes)
- G/g = gibibytes (1024^3 bytes)
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


def multiply_memory(memory_str, multiplier=1):
    """
    Multiply a memory specification by a factor, preserving the unit.

    Args:
        memory_str: Memory string (e.g., "8G", "512M", "1024K")
        multiplier: Multiplier (default: 1)

    Returns:
        Memory string with multiplied value (e.g., "16G", "1024M", "2048K")

    Examples:
        {{ "8G" | multiply_memory(2) }}    -> "16G"
        {{ "512M" | multiply_memory(2) }}  -> "1024M"
    """
    memory_str = str(memory_str).strip()
    match = re.match(r"^(\d+(?:\.\d+)?)\s*([KMGT])?$", memory_str, re.IGNORECASE)
    if not match:
        raise ValueError(
            f"Invalid memory format: {memory_str}. "
            f"Expected format: '2G', '512M', '1024K' (case-insensitive)"
        )

    value_str, unit = match.groups()
    value = float(value_str) * multiplier

    # Convert to int if it's a whole number
    if value == int(value):
        value = int(value)

    unit = unit.upper() if unit else ""
    return f"{value}{unit}"


class FilterModule(object):
    """Ansible filter plugin class."""

    def filters(self):
        return {
            "jvm_memory_to_k8s": jvm_memory_to_k8s,
            "multiply_memory": multiply_memory,
        }
