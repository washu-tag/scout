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

# org.jline:jline-remote-telnet is JLine's Telnet *server* transport, bundled by
# Spark's spark-shell / connect REPL. The headless transformer worker never starts
# a REPL or telnet server, so the server-side DoS CVEs Trivy maps onto it are not
# reachable here. New CVEs keep landing in this niche module and their IDs churn
# (GHSA→CVE), so match the package family rather than chasing IDs in .trivyignore.yaml.
ignore {
	input.PkgName == "org.jline:jline-remote-telnet"
}
