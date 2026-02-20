.PHONY: all deps setup-k3s config-traefik install-helm install-k3s clone-scout install-gpu-op \
    install-postgres install-redis install-auth install-lake install-analytics install-orchestrator \
    install-extractor install-monitor install-jupyter install-launchpad install-voila install-staging \
    verify-node echo

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

# Staging node (air-gapped deployments)
install-staging:
	$(ANSIBLE_CMD) playbooks/staging.yaml

install-helm:
	$(ANSIBLE_CMD) playbooks/helm.yaml

install-k3s: deps install-helm
	$(ANSIBLE_CMD) playbooks/k3s.yaml

config-traefik: install-k3s
	$(ANSIBLE_CMD) playbooks/traefik.yaml

install-gpu-op:
	$(ANSIBLE_CMD) playbooks/gpu.yaml

setup-k3s: install-k3s config-traefik install-gpu-op

install-postgres:
	$(ANSIBLE_CMD) playbooks/postgres.yaml

install-redis:
	$(ANSIBLE_CMD) playbooks/redis.yaml

install-auth:
	$(ANSIBLE_CMD) playbooks/auth.yaml

install-lake:
	$(ANSIBLE_CMD) playbooks/lake.yaml

install-chat:
	$(ANSIBLE_CMD) playbooks/chatbot.yaml

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

install-voila:
	$(ANSIBLE_CMD) playbooks/voila.yaml

# For tests
install-orthanc:
	$(ANSIBLE_CMD) playbooks/orthanc.yaml

install-dcm4chee:
	$(ANSIBLE_CMD) playbooks/dcm4chee.yaml

install-mailhog:
	$(ANSIBLE_CMD) playbooks/mailhog.yaml

# Node verification - validates mounts, connectivity, and resources on cluster nodes.
# Unlike deployment targets, this runs against ALL cluster nodes (no -l FQDN limit)
# and does not use become (no sudo required).
#
# Usage:
#   make verify-node INVENTORY_FILE=/path/to/inventory.yaml
#
# Options:
#   VAULT_PASSWORD_ARG  Vault password flag (default: --vault-password-file vault/pwd.sh)
#   ADD                 Additional ansible-playbook flags
#
# Examples:
#   # Run against a specific inventory with vault prompt
#   make verify-node INVENTORY_FILE=../inventory/prod/demo01.yaml VAULT_PASSWORD_ARG="--ask-vault-pass"
#
#   # Limit to a specific node or group
#   make verify-node ADD="-l gpu_workers"
#   make verify-node ADD="-l tagprod-big-01"
#
#   # Run only one check category (mounts, connectivity, or resources)
#   make verify-node ADD="-e verify_node_command=mounts"
#
#   # Override ansible_user
#   make verify-node ADD="-e ansible_user=$USER"
VERIFY_CMD := $(ANSIBLE_DEBUG_ENV) ANSIBLE_CALLBACK_RESULT_FORMAT=yaml \
              ansible-playbook -v -i $(INVENTORY_FILE) \
              --diff $(VAULT_PASSWORD_ARG) -e ansible_become=false \
              $(ANSIBLE_STEP) $(ANSIBLE_ADDITIONAL_FLAGS)

verify-node:
	$(VERIFY_CMD) playbooks/verify-node.yaml
