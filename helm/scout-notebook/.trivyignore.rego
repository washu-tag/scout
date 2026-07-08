# Trivy Rego ignore-policy — companion to .trivyignore.yaml. This file
# suppresses whole package families whose findings are recurring and
# non-actionable; individual CVEs with per-item rationale stay in
# .trivyignore.yaml. The scan action (.github/actions/trivy-scan-image) wires
# this in as `ignore-policy` when the file is present, alongside `ignorefile`.
#
# linux-libc-dev ships only the Linux kernel's C headers, no runtime code.
# Containers run on the host kernel, so the upstream *kernel* CVEs that Trivy
# maps onto this package are not exploitable here, and nothing in this image
# compiles against these headers. Fixes only ever land in the base image, and
# new kernel-CVE batches publish constantly, so enumerating each ID by hand
# was recurring churn (see issue #478).
package trivy

default ignore = false

ignore {
	input.PkgName == "linux-libc-dev"
}
