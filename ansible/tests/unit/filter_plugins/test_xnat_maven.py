#!/usr/bin/env python3
"""
Unit tests for xnat_maven filter plugin.

Run with: pytest ansible/tests/unit/filter_plugins/test_xnat_maven.py -v
Or from ansible/: pytest tests/unit/filter_plugins/test_xnat_maven.py -v

maven_artifact_path is implemented twice on purpose: here, for the air-gapped
preflight that HEADs each artifact through the Nexus group, and in the plugin
installer's coordinate_path() shell function, which builds the URL the init
container actually fetches. The two must agree or the preflight validates a
different path than the deploy downloads, so the cases below are also the
contract that shell function is tested against.
"""

import sys
from pathlib import Path

# Add filter_plugins to path so we can import the module
filter_plugins_path = Path(__file__).parent.parent.parent.parent / "filter_plugins"
sys.path.insert(0, str(filter_plugins_path))

import pytest
from xnat_maven import maven_artifact_path


class TestMavenArtifactPath:
    """Test cases for the maven_artifact_path filter."""

    def test_full_coordinate_with_classifier(self):
        """groupId dots become path separators; the classifier suffixes the filename."""
        assert (
            maven_artifact_path("au.edu.qcif.xnat.openid:openid-auth-plugin:1.5.0:jar:xpl")
            == "au/edu/qcif/xnat/openid/openid-auth-plugin/1.5.0/openid-auth-plugin-1.5.0-xpl.jar"
        )
        assert (
            maven_artifact_path("org.nrg.xnatx.plugins:container-service:3.8.1:jar:fat")
            == "org/nrg/xnatx/plugins/container-service/3.8.1/container-service-3.8.1-fat.jar"
        )

    def test_packaging_defaults_to_jar(self):
        """The three-field form is the common case: packaging defaults, no classifier."""
        assert maven_artifact_path("com.example:simple:1.0") == "com/example/simple/1.0/simple-1.0.jar"

    def test_non_jar_packaging(self):
        assert (
            maven_artifact_path("com.example:war-thing:2.1:war")
            == "com/example/war-thing/2.1/war-thing-2.1.war"
        )

    def test_empty_packaging_field_falls_back_to_jar(self):
        """An empty packaging slot is how a classifier is given without one."""
        assert (
            maven_artifact_path("a.b.c.d:deep:9.9.9::classifieronly")
            == "a/b/c/d/deep/9.9.9/deep-9.9.9-classifieronly.jar"
        )

    @pytest.mark.parametrize("coordinates", ["justonefield", "a:b", ""])
    def test_too_few_fields_rejected(self, coordinates):
        with pytest.raises(ValueError):
            maven_artifact_path(coordinates)

    @pytest.mark.parametrize("coordinates", [":b:1", "a::1", "a:b:"])
    def test_empty_required_field_rejected(self, coordinates):
        """An empty groupId/artifactId/version used to yield a plausible but wrong
        path (":b:1" -> "/b/1/b-1.jar"), surfacing only as a confusing 404."""
        with pytest.raises(ValueError):
            maven_artifact_path(coordinates)
