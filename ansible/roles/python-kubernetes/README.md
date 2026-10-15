# Python Kubernetes package

This role installs `python3-kubernetes` from the host's package sources and verifies
that Python can import it. When it is already installed, the role leaves repository
configuration and signing keys unchanged, including on disconnected hosts.

For a connected Rocky, AlmaLinux or CentOS host missing the package, the role installs
the distribution's `epel-release` package first. Other distributions retain their
existing package sources.

For `package_proxy_mode: nexus` or `external`, the existing default proxy repository
is `epel-9`, targeting Enterprise Linux 9 x86_64. This is not a repository for other
distributions. The client checks RPM signatures with the EPEL 9 public key and
verifies HTTPS. If the key file is missing, Ansible downloads it on the first staging
host, or on the controller when there is no staging group, then copies it to the
client. `rpm_key` checks its fingerprint when importing it, before the proxy
repository is written. The key is not downloaded by the production client.

For a fully disconnected installation, provision the key at
`python_kubernetes_epel_key_path` before running the role. The default path is
`/etc/pki/rpm-gpg/RPM-GPG-KEY-EPEL-9`. Its fingerprint must match
`python_kubernetes_epel_key_fingerprint` when imported. Site mirrors may set
`python_kubernetes_epel_key_url`; key rotation requires an independently verified
fingerprint change. A disconnected host missing the package fails before repository
setup unless a package proxy is configured.

Run the task-flow regression tests with an Ansible Python environment:

```bash
python ansible/tests/unit/test_python_kubernetes_packages.py
```

These tests execute Ansible's role includes and conditions with package, file and
network actions intercepted. They cover installed hosts, offline failures, native
repository setup, delegated key retrieval and rejection before proxy configuration.
