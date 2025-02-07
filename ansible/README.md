# Ansible-orchestrated Scout installation

## Preparation
You will need to first install [Ansible](https://docs.ansible.com/ansible/latest/installation_guide/intro_installation.html)
and `make` per your OS/arch.

It is assumed that you have provisioned a node with access to a sufficiently large "local" disk
before running Ansible. We will use Rancher's local path provisioner for provisioning
our persistent volumes. You may modify `../helm/storageclass.yaml` to change this.

Start by copying `inventory.example.yaml` to `inventory.yaml`. You will want to replace the
`vars` section with the values that are appropriate for your installation. Note that `helm_plugins_dir` and `kubeconfig_yaml`
are default locations that you probably want to leave alone. 

Some of these values in `inventory.yaml` should be treated as secrets, which you can manage with Ansible vault. 
For example, if you put a password (or a script that returns a password in `vault/get_pwd.sh`, then you can run, for example:

```bash
openssl rand -hex 32 | ansible-vault encrypt_string --vault-password-file /vault/pwd.sh
```

which will give you a string that you can then place in the `inventory.yaml` as the value for the appropriate var.
When you run Ansible to install the playbook, this secret will be decoded using the vault password file
(so be sure that's accessible from where ever you are running Ansible).

## Execution
Once your inventory.yaml is set up, you should run:
```bash
make all
```

And that's it!

Currently, all services but the MinIO console require port forwarding to access. You will need to have a local installation
of `kubectl` and update your kube config with values from the server running the Scout stack. Then, you may run:
```
kubectl port-forward -n grafana service/grafana 3000 &
kubectl port-forward -n temporal service/temporal-web 8080 &
kubectl port-forward -n prometheus service/prometheus-server 9090 &
```
