"""Parity tests for the stdlib port of ``jvm_memory_to_k8s``.

These are the SAME vectors that guard the Ansible filter
(``ansible/tests/unit/filter_plugins/test_jvm_memory.py`` ::
``TestJvmMemoryToK8s``). Reusing them proves the config-artifact lane and the
Ansible lane compute byte-identical memory strings.
"""

import pytest

from jvm_memory import jvm_memory_to_k8s


class TestJvmMemoryToK8s:
    """Test cases for jvm_memory_to_k8s (mirrors the Ansible filter's suite)."""

    def test_basic_conversions(self):
        """Basic G/M/K conversions with case-insensitivity and whitespace."""
        # Gigabytes (case insensitive)
        assert jvm_memory_to_k8s("1G") == "1Gi"
        assert jvm_memory_to_k8s("2g") == "2Gi"
        assert jvm_memory_to_k8s("16G") == "16Gi"

        # Megabytes (with whitespace)
        assert jvm_memory_to_k8s("512M") == "512Mi"
        assert jvm_memory_to_k8s(" 512M ") == "512Mi"
        assert jvm_memory_to_k8s("1024M") == "1Gi"  # Smart conversion

        # Kilobytes (case + whitespace)
        assert jvm_memory_to_k8s("1024k") == "1Mi"  # Smart conversion
        assert jvm_memory_to_k8s("\t2048K\t") == "2Mi"
        assert jvm_memory_to_k8s("512K") == "512Ki"

    def test_multipliers(self):
        """Multipliers (integer and float) for resource requests/limits."""
        # Integer multipliers
        assert jvm_memory_to_k8s("2G", 1) == "2Gi"
        assert jvm_memory_to_k8s("1G", 2) == "2Gi"
        assert jvm_memory_to_k8s("512M", 2) == "1Gi"
        assert jvm_memory_to_k8s("1G", 3) == "3Gi"
        assert jvm_memory_to_k8s("1G", 4) == "4Gi"

        # Float multipliers
        assert jvm_memory_to_k8s("2G", 0.5) == "1Gi"
        assert jvm_memory_to_k8s("8G", 1.5) == "12Gi"
        assert jvm_memory_to_k8s("6G", 1.0 / 3.0) == "2Gi"
        assert jvm_memory_to_k8s("512M", 4) == "2Gi"
        assert jvm_memory_to_k8s("1.5G", 1.5) == "2304Mi"

    def test_smart_unit_conversion(self):
        """Smart conversion to larger units when divisible, plus edge cases."""
        # Converts to Gi when divisible
        assert jvm_memory_to_k8s("2048M") == "2Gi"
        assert jvm_memory_to_k8s("1024K") == "1Mi"

        # Stays in original unit when not cleanly divisible
        assert jvm_memory_to_k8s("768M") == "768Mi"
        assert jvm_memory_to_k8s("1536M") == "1536Mi"

        # Edge cases: zero and very large values
        assert jvm_memory_to_k8s("0G") == "0Gi"
        assert jvm_memory_to_k8s("128G") == "128Gi"

    def test_invalid_formats(self):
        """Various invalid format errors."""
        with pytest.raises(ValueError, match="Invalid heap size format"):
            jvm_memory_to_k8s("G")  # No number
        with pytest.raises(ValueError, match="Invalid heap size format"):
            jvm_memory_to_k8s("2@G")  # Special chars
        with pytest.raises(ValueError, match="Invalid heap size format"):
            jvm_memory_to_k8s("2GM")  # Multiple units
        with pytest.raises(ValueError, match="Invalid heap size format"):
            jvm_memory_to_k8s("two-gigs")  # Text

    def test_inventory_example_values(self):
        """Actual values from inventory.example.yaml with common multipliers."""
        # Cassandra, Elasticsearch, Spark, Jupyter typical configs
        assert jvm_memory_to_k8s("8G") == "8Gi"
        assert jvm_memory_to_k8s("8G", 2) == "16Gi"
        assert jvm_memory_to_k8s("16G", 2) == "32Gi"
        assert jvm_memory_to_k8s("24G") == "24Gi"

        # Elasticsearch with 2x and 4x multipliers
        assert jvm_memory_to_k8s("512M", 2) == "1Gi"
        assert jvm_memory_to_k8s("512M", 4) == "2Gi"
        assert jvm_memory_to_k8s("8G", 4) == "32Gi"

    def test_float_inputs_and_multipliers(self):
        """Float input values and float multipliers."""
        # Float inputs convert to Mi when not evenly divisible to Gi
        assert jvm_memory_to_k8s("1.5G") == "1536Mi"
        assert jvm_memory_to_k8s("0.5G") == "512Mi"
        assert jvm_memory_to_k8s("2.5G") == "2560Mi"

        # Float inputs with multipliers
        assert jvm_memory_to_k8s("1.5G", 2.0) == "3Gi"
        assert jvm_memory_to_k8s("0.5G", 4.0) == "2Gi"
        assert jvm_memory_to_k8s("1024M", 0.5) == "512Mi"

    def test_float_precision_rounding(self):
        """Edge cases in float arithmetic (division round-trips, multipliers)."""
        # (1024/3) * 3 should equal exactly 1024M = 1Gi
        third_of_1024 = 1024 / 3
        assert jvm_memory_to_k8s(f"{third_of_1024}M", 3) == "1Gi"

        # Complex float arithmetic
        assert jvm_memory_to_k8s("1.5G", 2.0 / 3.0) == "1Gi"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
