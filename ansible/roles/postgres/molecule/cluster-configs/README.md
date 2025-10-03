# Cluster Configuration Files

These configuration files allow you to test the postgres role against different Kubernetes clusters without modifying test code.

## Available Configurations

### local-k3s.yml
Configuration for testing against a local k3s cluster running on your development machine.

**Usage:**
```bash
export KUBECONFIG=~/.kube/config
molecule test -s cluster
```

### dev-cluster.yml
Configuration for testing against a remote development cluster (e.g., tagdev-control-03).

**Usage:**
```bash
export KUBECONFIG=~/.kube/scout/tagdev-control-03/config
molecule test -s cluster
```

## Creating Your Own Configuration

To add a new cluster configuration:

1. Copy an existing config file (e.g., `local-k3s.yml`)
2. Name it descriptively (e.g., `staging-cluster.yml`)
3. Update the values:
   - `kubeconfig_yaml`: Path to your cluster's kubeconfig
   - Resource limits appropriate for your cluster
   - **Note**: `postgres_dir` is set automatically by prepare.yml using tempfile
4. Set the environment variable and run tests:
   ```bash
   export KUBECONFIG=/path/to/your/kubeconfig
   molecule test -s cluster
   ```

## Configuration Variables

### Required
- `kubeconfig_yaml`: Path to Kubernetes config file
- `postgres_cluster_namespace`: Namespace for test resources (should be different from production)
- `postgres_user`, `postgres_password`: Test database credentials
- `ingest_postgres_table_name`: Test database name

### Automatically Set
- `postgres_dir`: Created dynamically by prepare.yml using ansible.builtin.tempfile

### Optional
- `postgres_resources`: CPU/memory requests and limits
- `postgres_storage_size`: Size of persistent volume
- `postgres_parameters`: PostgreSQL tuning parameters

## Notes

- Always use a separate namespace for testing (e.g., `postgres-molecule-test`)
- Test directories are created automatically using OS-appropriate temp locations
- Use test credentials, never production passwords
- Molecule will clean up Kubernetes resources after tests complete
- Temporary directories are cleaned up by the OS on reboot
