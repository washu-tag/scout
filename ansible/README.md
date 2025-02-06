# Ansible-orchestrated Scout installation

## Preparation
You will need to first install `Ansible` and `make`.

Before you run Ansible, it is assumed that you have provisioned your node. You will need to copy `inventory.example.yaml` 
to `inventory.yaml` and replace the `vars` section with the values you wish to use. Some of these values should be secrets,
which you can manage with Ansible vault. For example, if you put a password in `vault/pwd.txt` (or a script that 
retrieves a password), then you can run

```bash
openssl rand -hex 32 | ansible-vault encrypt_string --vault-password-file /vault/pwd.txt
```

which will give you a string that you can then place in the `inventory.yaml` as the value for the appropriate var.

## Execution
Once your inventory.yaml is set up, you should run:
```bash
make all
```

And that's it!

Currently, all services but the MinIO console require port forwarding to access.

