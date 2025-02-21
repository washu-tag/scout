.PHONY: all deps install-k3s install-helm clone-scout install-minio \
        install-orchestrator install-prometheus install-jaeger install-grafana echo

# Variables
VAULT_PASSWORD_ARG  ?= --vault-password-file vault/pwd.sh
INVENTORY_FILE      ?= inventory.yaml
FQDN                := $(shell hostname -f)

# Conditionally prepend ANSIBLE_DEBUGGER=always if ANSIBLE_DEBUGGER is set
ANSIBLE_DEBUG_ENV   := $(if $(DEBUG),ANSIBLE_ENABLE_TASK_DEBUGGER=True,)
ANSIBLE_STEP        := $(if $(DEBUG),--step,)

# Conditionally omit --diff if NODIFF is set
DIFF_FLAG           := $(if $(NODIFF),, --diff)

# Common ansible-playbook command
ANSIBLE_CMD         := $(ANSIBLE_DEBUG_ENV) ansible-playbook -v -i $(INVENTORY_FILE) \
                       $(DIFF_FLAG) $(VAULT_PASSWORD_ARG) -l $(FQDN) $(ANSIBLE_STEP)

all: deps
	$(ANSIBLE_CMD) playbooks/main.yaml

echo:
	echo "FQDN: $(FQDN)"
	echo "Inventory: $(INVENTORY_FILE)"
	echo "Vault Password Arg: $(VAULT_PASSWORD_ARG)"
	echo "Ansible Debugger, step: $(ANSIBLE_DEBUG_ENV),  $(ANSIBLE_STEP)"
	echo "Diff Flag: $(DIFF_FLAG)"
	echo "Full command: $(ANSIBLE_CMD)"

deps:
	ansible-galaxy install -r collections/requirements.yaml

clone-scout:
	$(ANSIBLE_CMD) playbooks/scout.yaml

install-k3s: deps
	$(ANSIBLE_CMD) playbooks/k3s.yaml

install-helm: install-k3s
	$(ANSIBLE_CMD) playbooks/helm.yaml

install-minio:
	$(ANSIBLE_CMD) playbooks/minio.yaml

install-orchestrator:
	$(ANSIBLE_CMD) playbooks/orchestrator.yaml

install-prometheus:
	$(ANSIBLE_CMD) playbooks/prometheus.yaml

install-jaeger:
	$(ANSIBLE_CMD) playbooks/jaeger.yaml

install-grafana:
	$(ANSIBLE_CMD) playbooks/grafana.yaml
