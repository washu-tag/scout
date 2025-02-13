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
For example, if you put a script that returns a password in `vault/pwd.sh` (make sure this file is executable: 
`chmod 755 vault/pwd.sh`):
```bash
#!/bin/bash
# Silly example, you could just put this in a file called password.txt and remove the echo
echo "mypassword"
```

or, more usefully, retrieve a password from Bitwarden:
```
#!/bin/bash
# Ensure BW_SESSION is set
if [ -z "$BW_SESSION" ]; then
  echo "Error: BW_SESSION is not set. Please log in to Bitwarden first."
  exit 1
fi

# Fetch the password from Bitwarden
bw get password "AnsibleVault" 2>/dev/null
```


Then you can run, e.g.:

```bash
openssl rand -hex 32 | ansible-vault encrypt_string --vault-password-file vault/pwd.sh
```

which will give you an encoded secret string that you can then place in the `inventory.yaml` as the value for the 
appropriate var. When you run Ansible to install the playbook, this secret will be decoded using the vault 
password script (so be sure that's accessible from where ever you are running Ansible).

There is plenty more you can do with Ansible Vault. To read more, please see the [Ansible documentation on managing vault passwords](https://docs.ansible.com/ansible/latest/vault_guide/vault_managing_passwords.html).

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
