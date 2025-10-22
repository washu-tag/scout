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

    def test_gigabytes_uppercase(self):
        """Test conversion of gigabytes with uppercase G."""
        assert jvm_memory_to_k8s("1G") == "1Gi"
        assert jvm_memory_to_k8s("2G") == "2Gi"
        assert jvm_memory_to_k8s("16G") == "16Gi"

    def test_gigabytes_lowercase(self):
        """Test conversion of gigabytes with lowercase g (case-insensitive)."""
        assert jvm_memory_to_k8s("1g") == "1Gi"
        assert jvm_memory_to_k8s("2g") == "2Gi"
        assert jvm_memory_to_k8s("16g") == "16Gi"

    def test_megabytes_uppercase(self):
        """Test conversion of megabytes with uppercase M."""
        assert jvm_memory_to_k8s("512M") == "512Mi"
        assert jvm_memory_to_k8s("1024M") == "1Gi"  # Smart conversion
        assert jvm_memory_to_k8s("2048M") == "2Gi"  # Smart conversion

    def test_megabytes_lowercase(self):
        """Test conversion of megabytes with lowercase m (case-insensitive)."""
        assert jvm_memory_to_k8s("512m") == "512Mi"
        assert jvm_memory_to_k8s("1024m") == "1Gi"  # Smart conversion

    def test_kilobytes_uppercase(self):
        """Test conversion of kilobytes with uppercase K."""
        assert jvm_memory_to_k8s("1024K") == "1Mi"  # Smart conversion
        assert jvm_memory_to_k8s("2048K") == "2Mi"  # Smart conversion
        assert jvm_memory_to_k8s("512K") == "512Ki"

    def test_kilobytes_lowercase(self):
        """Test conversion of kilobytes with lowercase k (case-insensitive)."""
        assert jvm_memory_to_k8s("1024k") == "1Mi"  # Smart conversion
        assert jvm_memory_to_k8s("512k") == "512Ki"

    def test_multiplier_2x(self):
        """Test 2x multiplier for limits (off-heap/overhead memory)."""
        assert jvm_memory_to_k8s("1G", 2) == "2Gi"
        assert jvm_memory_to_k8s("2G", 2) == "4Gi"
        assert jvm_memory_to_k8s("8G", 2) == "16Gi"
        assert jvm_memory_to_k8s("512M", 2) == "1Gi"
        assert jvm_memory_to_k8s("256M", 2) == "512Mi"

    def test_multiplier_3x(self):
        """Test 3x multiplier (edge case)."""
        assert jvm_memory_to_k8s("1G", 3) == "3Gi"
        assert jvm_memory_to_k8s("512M", 3) == "1536Mi"

    def test_multiplier_1x_explicit(self):
        """Test explicit 1x multiplier (same as default)."""
        assert jvm_memory_to_k8s("2G", 1) == "2Gi"
        assert jvm_memory_to_k8s("512M", 1) == "512Mi"

    def test_whitespace_handling(self):
        """Test that leading/trailing whitespace is handled."""
        assert jvm_memory_to_k8s(" 2G ") == "2Gi"
        assert jvm_memory_to_k8s("  512M  ") == "512Mi"
        assert jvm_memory_to_k8s("\t1G\t") == "1Gi"

    def test_mixed_case(self):
        """Test mixed case handling (should work case-insensitively)."""
        # Note: Only the unit suffix is case-insensitive, not the number
        assert jvm_memory_to_k8s("2g") == "2Gi"
        assert jvm_memory_to_k8s("2G") == "2Gi"

    def test_smart_conversion_1024M_to_Gi(self):
        """Test smart conversion of 1024M to Gi."""
        assert jvm_memory_to_k8s("1024M") == "1Gi"
        assert jvm_memory_to_k8s("2048M") == "2Gi"
        assert jvm_memory_to_k8s("3072M") == "3Gi"

    def test_smart_conversion_1024K_to_Mi(self):
        """Test smart conversion of 1024K to Mi."""
        assert jvm_memory_to_k8s("1024K") == "1Mi"
        assert jvm_memory_to_k8s("2048K") == "2Mi"

    def test_non_divisible_megabytes(self):
        """Test megabytes that don't convert cleanly to Gi."""
        assert jvm_memory_to_k8s("768M") == "768Mi"
        assert jvm_memory_to_k8s("1536M") == "1536Mi"  # 1.5 Gi but stays as Mi

    def test_non_divisible_kilobytes(self):
        """Test kilobytes that don't convert cleanly to Mi."""
        assert jvm_memory_to_k8s("512K") == "512Ki"
        assert jvm_memory_to_k8s("768K") == "768Ki"

    def test_common_cassandra_values(self):
        """Test common Cassandra heap sizes."""
        # Dev
        assert jvm_memory_to_k8s("2G") == "2Gi"
        assert jvm_memory_to_k8s("2G", 2) == "4Gi"
        # Prod
        assert jvm_memory_to_k8s("16G") == "16Gi"
        assert jvm_memory_to_k8s("16G", 2) == "32Gi"

    def test_common_elasticsearch_values(self):
        """Test common Elasticsearch heap sizes."""
        # Dev
        assert jvm_memory_to_k8s("1G") == "1Gi"
        assert jvm_memory_to_k8s("1G", 2) == "2Gi"
        # Prod
        assert jvm_memory_to_k8s("8G") == "8Gi"
        assert jvm_memory_to_k8s("8G", 2) == "16Gi"

    def test_common_spark_values(self):
        """Test common Spark memory sizes."""
        # Dev
        assert jvm_memory_to_k8s("1G") == "1Gi"
        assert jvm_memory_to_k8s("1G", 2) == "2Gi"
        # Prod
        assert jvm_memory_to_k8s("24G") == "24Gi"
        assert jvm_memory_to_k8s("24G", 2) == "48Gi"

    def test_invalid_format_no_number(self):
        """Test invalid format with no number."""
        with pytest.raises(ValueError, match="Invalid heap size format"):
            jvm_memory_to_k8s("G")

    def test_invalid_format_special_chars(self):
        """Test invalid format with special characters."""
        with pytest.raises(ValueError, match="Invalid heap size format"):
            jvm_memory_to_k8s("2@G")

    def test_invalid_format_multiple_units(self):
        """Test invalid format with multiple units."""
        with pytest.raises(ValueError, match="Invalid heap size format"):
            jvm_memory_to_k8s("2GM")

    def test_invalid_format_text(self):
        """Test invalid format with text."""
        with pytest.raises(ValueError, match="Invalid heap size format"):
            jvm_memory_to_k8s("two-gigs")

    def test_edge_case_zero(self):
        """Test edge case of zero memory (technically valid)."""
        assert jvm_memory_to_k8s("0G") == "0Gi"
        assert jvm_memory_to_k8s("0M") == "0Mi"

    def test_edge_case_very_large(self):
        """Test very large memory values."""
        assert jvm_memory_to_k8s("128G") == "128Gi"
        assert jvm_memory_to_k8s("256G") == "256Gi"

    def test_inventory_example_values(self):
        """Test actual values from inventory.example.yaml."""
        # Cassandra prod
        assert jvm_memory_to_k8s("8G") == "8Gi"
        assert jvm_memory_to_k8s("16G") == "16Gi"
        assert jvm_memory_to_k8s("16G", 2) == "32Gi"

        # Elasticsearch prod
        assert jvm_memory_to_k8s("8G") == "8Gi"
        assert jvm_memory_to_k8s("8G", 2) == "16Gi"

        # Spark prod
        assert jvm_memory_to_k8s("24G") == "24Gi"
        assert jvm_memory_to_k8s("24G", 2) == "48Gi"

        # Jupyter prod
        assert jvm_memory_to_k8s("8G") == "8Gi"
        assert jvm_memory_to_k8s("8G", 2) == "16Gi"


class TestMultiplyMemory:
    """Test cases for multiply_memory filter."""

    def test_gigabytes_uppercase(self):
        """Test multiplying gigabytes with uppercase G."""
        assert multiply_memory("8G", 2) == "16G"
        assert multiply_memory("4G", 2) == "8G"
        assert multiply_memory("1G", 2) == "2G"

    def test_gigabytes_lowercase(self):
        """Test multiplying gigabytes with lowercase g."""
        assert multiply_memory("8g", 2) == "16G"
        assert multiply_memory("4g", 2) == "8G"

    def test_megabytes(self):
        """Test multiplying megabytes."""
        assert multiply_memory("512M", 2) == "1024M"
        assert multiply_memory("256M", 2) == "512M"
        assert multiply_memory("1024M", 2) == "2048M"

    def test_kilobytes(self):
        """Test multiplying kilobytes."""
        assert multiply_memory("1024K", 2) == "2048K"
        assert multiply_memory("512K", 2) == "1024K"

    def test_multiplier_1x(self):
        """Test 1x multiplier (no change)."""
        assert multiply_memory("8G", 1) == "8G"
        assert multiply_memory("512M", 1) == "512M"

    def test_multiplier_3x(self):
        """Test 3x multiplier."""
        assert multiply_memory("8G", 3) == "24G"
        assert multiply_memory("2G", 3) == "6G"

    def test_jupyter_common_values(self):
        """Test common JupyterHub memory values."""
        assert multiply_memory("8G", 2) == "16G"
        assert multiply_memory("1G", 2) == "2G"
        assert multiply_memory("24G", 2) == "48G"

    def test_whitespace_handling(self):
        """Test that leading/trailing whitespace is handled."""
        assert multiply_memory(" 8G ", 2) == "16G"
        assert multiply_memory("  512M  ", 2) == "1024M"

    def test_invalid_format(self):
        """Test invalid format raises error."""
        with pytest.raises(ValueError, match="Invalid memory format"):
            multiply_memory("invalid", 2)
        with pytest.raises(ValueError, match="Invalid memory format"):
            multiply_memory("8Gi", 2)  # Should use K, M, G, T (not Ki, Mi, Gi)


if __name__ == "__main__":
    # Allow running tests directly with python
    pytest.main([__file__, "-v"])
