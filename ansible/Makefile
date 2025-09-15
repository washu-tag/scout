.PHONY: all deps install-k3s config-traefik install-helm clone-scout install-postgres \
        install-lake install-analytics install-orchestrator install-extractor \
        install-monitor install-jupyter install-launchpad echo

# Variables
VAULT_PASSWORD_ARG  ?= --vault-password-file vault/pwd.sh
INVENTORY_FILE      ?= inventory.yaml
FQDN                := $(shell hostname -f)

# Conditionally prepend ANSIBLE_DEBUGGER=always if ANSIBLE_DEBUGGER is set
ANSIBLE_DEBUG_ENV   := $(if $(DEBUG),ANSIBLE_ENABLE_TASK_DEBUGGER=True,)
ANSIBLE_STEP        := $(if $(DEBUG),--step,)

# Conditionally omit --diff if NODIFF is set
DIFF_FLAG           := $(if $(NODIFF),, --diff)

# Additional flags/options
ANSIBLE_ADDITIONAL_FLAGS := $(if $(ADD),$(ADD),)

# Common ansible-playbook command
ANSIBLE_CMD         := $(ANSIBLE_DEBUG_ENV) ansible-playbook -v -i $(INVENTORY_FILE) \
                       $(DIFF_FLAG) $(VAULT_PASSWORD_ARG) -l $(FQDN) $(ANSIBLE_STEP) $(ANSIBLE_ADDITIONAL_FLAGS)

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

config-traefik: install-k3s
	$(ANSIBLE_CMD) playbooks/traefik.yaml

install-helm:
	$(ANSIBLE_CMD) playbooks/helm.yaml

install-postgres:
	$(ANSIBLE_CMD) playbooks/postgres.yaml

install-auth:
	$(ANSIBLE_CMD) playbooks/auth.yaml

install-lake:
	$(ANSIBLE_CMD) playbooks/lake.yaml

install-analytics:
	$(ANSIBLE_CMD) playbooks/analytics.yaml

install-orchestrator:
	$(ANSIBLE_CMD) playbooks/orchestrator.yaml

install-extractor:
	$(ANSIBLE_CMD) playbooks/extractor.yaml

install-jupyter:
	$(ANSIBLE_CMD) playbooks/jupyter.yaml

install-monitor:
	$(ANSIBLE_CMD) playbooks/monitor.yaml

install-launchpad:
	$(ANSIBLE_CMD) playbooks/launchpad.yaml

install-orthanc:
	$(ANSIBLE_CMD) playbooks/orthanc.yaml

install-dcm4chee:
	$(ANSIBLE_CMD) playbooks/dcm4chee.yaml

install-mailhog:
	$(ANSIBLE_CMD) playbooks/mailhog.yaml
