# Molecule Testing for Ansible Roles

This guide explains how to use Molecule to test Ansible roles in the Scout project. Molecule is a testing framework that helps you develop and test roles in isolation before deploying to production.

## Overview

Molecule provides:
- **Unit testing** - Test role logic without real infrastructure (variable resolution, template rendering, task syntax)
- **Automated workflows** - Create, converge, verify, and destroy test environments
- **Multiple scenarios** - Different test configurations for different purposes

## Prerequisites

### Install Molecule

You can install Molecule either globally or use `uvx` to run it in an isolated environment:

**Option 1: Using uvx (Recommended)**

```bash
# Install uv if not already installed
# macOS/Linux: curl -LsSf https://astral.sh/uv/install.sh | sh

# Run molecule with uvx (no installation needed)
uvx molecule --version

# Run molecule commands
uvx molecule test -s default
```

**Option 2: Global Installation**

```bash
# Install molecule with ansible support
pip3 install molecule molecule-plugins[docker] ansible-core

# Verify installation
molecule --version
```

### Required Collections

Ensure you have the necessary Ansible collections:

```bash
ansible-galaxy collection install kubernetes.core
ansible-galaxy collection install ansible.utils
```

## Role Structure

Roles with Molecule tests follow this structure:

```
roles/<role>/
├── defaults/
│   └── main.yaml
├── tasks/
│   └── main.yaml
├── templates/
├── meta/
│   └── main.yaml
└── molecule/
    └── default/              # Unit tests
        ├── molecule.yml
        ├── converge.yml
        ├── verify.yml
        └── prepare.yml
```

## Test Scenarios

### Default Scenario (Unit Tests)

Fast tests that don't require real infrastructure. Tests:
- Variable resolution
- Template rendering
- Task syntax
- Role logic

**Note:** The converge step is typically a no-op since there's no real infrastructure to deploy to. Actual validation happens in the verify step.

**Run:**
```bash
cd roles/<role>
molecule test -s default
# or with uvx
uvx molecule test -s default
```

**Speed:** Seconds to minutes

## Common Molecule Commands

### Full Test Cycle

Runs complete test sequence: dependency → destroy → create → prepare → converge → verify → destroy

```bash
molecule test -s <scenario>
```

### Iterative Development

When developing roles, use these commands for faster feedback:

```bash
# Apply the role (dependency + create + prepare + converge)
molecule converge -s <scenario>

# Run verification tests only
molecule verify -s <scenario>

# Clean up test resources
molecule destroy -s <scenario>

# Reset molecule state (clears cache)
molecule reset -s <scenario>
```

### Debugging

```bash
# Keep test environment after failure
molecule --debug test -s <scenario>

# Login to test environment
molecule login -s <scenario>

# Show test matrix
molecule matrix test -s <scenario>
```

## Writing Tests

### Converge Playbook (converge.yml)

Applies the role to test hosts. For unit test scenarios, this is typically a no-op with explanatory output:

```yaml
---
- name: Converge
  hosts: localhost
  gather_facts: false
  tasks:
    - name: Explain scenario purpose
      ansible.builtin.debug:
        msg: |
          This scenario tests role logic without K8s deployment.
          Actual validation happens in verify.yml.
```

### Verify Playbook (verify.yml)

Asserts that the role behaved correctly. For unit tests, this typically validates variable resolution and template rendering:

```yaml
---
- name: Verify
  hosts: localhost
  gather_facts: false
  tasks:
    - name: Include role to get variables
      ansible.builtin.include_role:
        name: <role>
        tasks_from: main

    - name: Verify variables are defined correctly
      ansible.builtin.assert:
        that:
          - <role>_variable is defined
          - <role>_variable == "expected_value"
        fail_msg: "Variable not set correctly"
        success_msg: "✓ Variables configured correctly"
```

### Prepare Playbook (prepare.yml)

Sets up test prerequisites and creates necessary directories:

```yaml
---
- name: Prepare
  hosts: localhost
  gather_facts: false
  tasks:
    - name: Create test directory
      ansible.builtin.file:
        path: /tmp/molecule_test_dir
        state: directory
        mode: '0755'
```

## Configuration Files

### molecule.yml

Main configuration for test scenario:

```yaml
---
dependency:
  name: galaxy

driver:
  name: default

platforms:
  - name: localhost
    groups:
      - k3s_cluster
      - server

provisioner:
  name: ansible
  env:
    ANSIBLE_ROLES_PATH: ../../roles
    KUBECONFIG: "${KUBECONFIG:-~/.kube/config}"
    K8S_AUTH_KUBECONFIG: "${KUBECONFIG:-~/.kube/config}"
  inventory:
    group_vars:
      all:
        ansible_connection: local
        gather_facts: false
        
        # Test paths - created by prepare.yml
        kubeconfig_yaml: /tmp/molecule_<role>_kubeconfig.yaml
        <role>_dir: /tmp/molecule_<role>_test
        
        # Test namespace and credentials
        <role>_namespace: <role>-molecule-test
        # ...

verifier:
  name: ansible
```

## Best Practices

### Test Isolation

- Use dedicated test namespaces (e.g., `<role>-molecule-test`)
- Use test credentials, never production passwords
- Test directories in `/tmp` or similar temporary locations
- Clean up resources after tests

### Test Speed

- Keep unit tests fast (< 1 minute)
- Run unit tests frequently during development

### Test Coverage

Test these aspects of your role:
1. ✅ Variable resolution and defaults
2. ✅ Template rendering
3. ✅ Required variables are defined
4. ✅ Resources are created correctly
5. ✅ Idempotence (running twice doesn't change state)
6. ✅ Error handling

### CI/CD Integration

```yaml
# .github/workflows/test.yml example
name: Test Roles
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v2
      - name: Install Molecule
        run: pip3 install molecule molecule-plugins[docker] ansible-core
      - name: Run mock tests
        run: |
          cd roles/<role>
          molecule test -s default
```

## Troubleshooting

### "Unable to parse inventory"
Ensure you're running commands from the role directory:
```bash
cd roles/<role>
molecule test
```

### "Failed to import the required Python library"
This happens when Ansible uses a different Python than Molecule. The molecule.yml should include:
```yaml
inventory:
  group_vars:
    all:
      ansible_python_interpreter: "{{ ansible_playbook_python }}"
```

This is especially important when using `uvx` which runs Molecule in an isolated environment.


### "Role not found" errors
Ensure roles_path is configured correctly in molecule.yml:
```yaml
config_options:
  defaults:
    roles_path: ${MOLECULE_PROJECT_DIRECTORY}/..
```

## Example Workflow

### Developing a New Role

```bash
# 1. Create role structure
ansible-galaxy role init <role>

# 2. Write role tasks
vim roles/<role>/tasks/main.yaml

# 3. Run unit tests frequently
cd roles/<role>
molecule converge -s default
molecule verify -s default

# 4. Clean up
molecule destroy -s default
```

### Testing Changes to Existing Role

```bash
cd roles/<role>

# Quick feedback loop
molecule converge -s default
molecule verify -s default

# Full test before committing
molecule test -s default

# If tests fail
molecule --debug converge -s default
# ... debug and fix ...
molecule destroy -s default
```

## Additional Resources

- [Molecule Documentation](https://ansible.readthedocs.io/projects/molecule/)
- [Ansible Testing Strategies](https://docs.ansible.com/ansible/latest/reference_appendices/test_strategies.html)
- [Scout REFACTORING.md](../REFACTORING.md) - Role-based architecture

## Example: Testing the Postgres Role

### Default Scenario (Unit Tests)

```bash
cd roles/postgres

# Run full test cycle
molecule test -s default

# Or iterative development
molecule converge -s default
molecule verify -s default
molecule destroy -s default
```

**What it tests:**
- Variable resolution and defaults
- Template rendering (Jinja2 syntax)
- Storage definitions structure
- Role parameters configuration

**Test artifacts created:**
- `/tmp/molecule_postgres_kubeconfig.yaml` - Mock kubeconfig
- `/tmp/molecule_postgres_test/` - Test postgres directory

## Getting Help

If you encounter issues with Molecule tests:
1. Check this documentation
2. Review the role's `molecule/` directory for scenario-specific notes
3. Check the [Molecule GitHub Issues](https://github.com/ansible/molecule/issues)
4. Ask in team chat or open a GitHub issue in the Scout repository
