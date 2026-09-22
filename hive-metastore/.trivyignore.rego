# Trivy Rego ignore-policy (wired in by .github/actions/trivy-scan-image). Gate only
# on the jars this image adds (auxlib/, from build.gradle). Everything else is the
# upstream starburstdata/hive image, which Scout neither builds nor can patch here;
# its findings are the same with or without this layer. OS packages carry no
# PkgPath, hence the default.
package trivy

default ignore = false

ignore {
	not startswith(object.get(input, "PkgPath", ""), "opt/apache-hive-3.1.3-bin/auxlib/")
}
