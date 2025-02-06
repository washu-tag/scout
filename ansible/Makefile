.PHONY: all deps install-k3s install-helm clone-scout install-minio install-orchestrator install-prometheus install-jaeger install-grafana

all: deps install-k3s install-helm clone-scout install-minio install-orchestrator install-prometheus install-jaeger install-grafana

deps:
	ansible-galaxy install -r collections/requirements.yaml

install-k3s:
	ansible-playbook -v -i inventory.yaml --diff playbooks/k3s-cluster.yaml --vault-password-file vault/pwd.txt

install-helm:
	ansible-playbook -v -i inventory.yaml --diff playbooks/helm.yaml --vault-password-file vault/pwd.txt

clone-scout:
	ansible-playbook -v -i inventory.yaml --diff playbooks/scout.yaml

install-minio:
	ansible-playbook -v -i inventory.yaml --diff playbooks/minio.yaml --vault-password-file vault/pwd.txt

install-orchestrator:
	ansible-playbook -v -i inventory.yaml --diff playbooks/orchestrator.yaml --vault-password-file vault/pwd.txt

install-prometheus:
	ansible-playbook -v -i inventory.yaml --diff playbooks/prometheus.yaml

install-jaeger:
	ansible-playbook -v -i inventory.yaml --diff playbooks/jaeger.yaml

install-grafana:
	ansible-playbook -v -i inventory.yaml --diff playbooks/grafana.yaml --vault-password-file vault/pwd.txt
