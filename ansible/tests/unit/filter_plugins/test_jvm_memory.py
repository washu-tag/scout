#!/usr/bin/env python3
"""
Unit tests for jvm_memory filter plugin.

Run with: pytest ansible/tests/unit/filter_plugins/test_jvm_memory.py -v
Or from ansible/: pytest tests/unit/filter_plugins/test_jvm_memory.py -v
"""

import sys
from pathlib import Path

# Add filter_plugins to path so we can import the module
filter_plugins_path = Path(__file__).parent.parent.parent.parent / "filter_plugins"
sys.path.insert(0, str(filter_plugins_path))

import pytest
from jvm_memory import jvm_memory_to_k8s, multiply_memory


class TestJvmMemoryToK8s:
    """Test cases for jvm_memory_to_k8s filter."""

    def test_basic_conversions(self):
        """Test basic JVM memory unit conversions (G, M, K) with case-insensitivity and whitespace."""
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
        """Test multipliers (integer and float) for resource requests/limits."""
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
        """Test smart conversion to larger units when divisible, plus edge cases."""
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
        """Test various invalid format errors."""
        with pytest.raises(ValueError, match="Invalid heap size format"):
            jvm_memory_to_k8s("G")  # No number
        with pytest.raises(ValueError, match="Invalid heap size format"):
            jvm_memory_to_k8s("2@G")  # Special chars
        with pytest.raises(ValueError, match="Invalid heap size format"):
            jvm_memory_to_k8s("2GM")  # Multiple units
        with pytest.raises(ValueError, match="Invalid heap size format"):
            jvm_memory_to_k8s("two-gigs")  # Text

    def test_inventory_example_values(self):
        """Test actual values from inventory.example.yaml with common multipliers."""
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
        """Test float input values and float multipliers."""
        # Float inputs convert to Mi when not evenly divisible to Gi
        assert jvm_memory_to_k8s("1.5G") == "1536Mi"
        assert jvm_memory_to_k8s("0.5G") == "512Mi"
        assert jvm_memory_to_k8s("2.5G") == "2560Mi"

        # Float inputs with multipliers
        assert jvm_memory_to_k8s("1.5G", 2.0) == "3Gi"
        assert jvm_memory_to_k8s("0.5G", 4.0) == "2Gi"
        assert jvm_memory_to_k8s("1024M", 0.5) == "512Mi"

    def test_float_precision_rounding(self):
        """
        This test verifies edge cases that might theoretically be problematic:
        - Division operations (e.g., 1024/3 * 3)
        - Complex float arithmetic with multipliers
        """
        # (1024/3) * 3 should equal exactly 1024M = 1Gi
        third_of_1024 = 1024 / 3
        assert jvm_memory_to_k8s(f"{third_of_1024}M", 3) == "1Gi"

        # Complex float arithmetic
        assert jvm_memory_to_k8s("1.5G", 2.0 / 3.0) == "1Gi"


class TestMultiplyMemory:
    """Test cases for multiply_memory filter."""

    def test_basic_operations(self):
        """Test basic multiplication across units with case-insensitivity."""
        # Different units (case insensitive)
        assert multiply_memory("8G", 2) == "16G"
        assert multiply_memory("8g", 2) == "16G"
        assert multiply_memory("512M", 2) == "1024M"
        assert multiply_memory("1024K", 2) == "2048K"

        # Different multipliers
        assert multiply_memory("8G", 1) == "8G"
        assert multiply_memory("8G", 3) == "24G"

        # Whitespace handling
        assert multiply_memory(" 8G ", 2) == "16G"

    def test_invalid_formats(self):
        """Test invalid format errors."""
        with pytest.raises(ValueError, match="Invalid memory format"):
            multiply_memory("invalid", 2)
        with pytest.raises(ValueError, match="Invalid memory format"):
            multiply_memory("8Gi", 2)  # Should use K, M, G, T (not Ki, Mi, Gi)

    def test_float_operations(self):
        """Test float multipliers and float inputs."""
        # Float multipliers with integer results
        assert multiply_memory("8G", 0.5) == "4G"
        assert multiply_memory("8G", 1.5) == "12G"

        # Float multipliers with decimal results
        assert multiply_memory("3G", 1.5) == "4.5G"

        # Float inputs with multipliers
        assert multiply_memory("1.5G", 1.5) == "2.25G"
        assert multiply_memory("1.5G", 2) == "3G"
        assert multiply_memory("0.5G", 3.0) == "1.5G"


if __name__ == "__main__":
    # Allow running tests directly with python
    pytest.main([__file__, "-v"])
