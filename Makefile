.PHONY: all deps install-k3s install-helm clone-scout install-minio install-orchestrator install-prometheus install-jaeger install-grafana

all: deps clone-scout install-k3s install-helm install-minio install-orchestrator install-prometheus install-jaeger install-grafana

deps:
	ansible-galaxy install -r collections/requirements.yaml

clone-scout:
	ansible-playbook -v -i inventory.yaml --diff playbooks/scout.yaml -l $$(hostnamectl --static)

install-k3s:
	ansible-playbook -v -i inventory.yaml --diff playbooks/k3s.yaml --vault-password-file vault/pwd.sh -l $$(hostnamectl --static)

install-helm:
	ansible-playbook -v -i inventory.yaml --diff playbooks/helm.yaml --vault-password-file vault/pwd.sh -l $$(hostnamectl --static)

install-minio:
	ansible-playbook -v -i inventory.yaml --diff playbooks/minio.yaml --vault-password-file vault/pwd.sh -l $$(hostnamectl --static)

install-orchestrator:
	ansible-playbook -v -i inventory.yaml --diff playbooks/orchestrator.yaml --vault-password-file vault/pwd.sh -l $$(hostnamectl --static)

install-prometheus:
	ansible-playbook -v -i inventory.yaml --diff playbooks/prometheus.yaml -l $$(hostnamectl --static)

install-jaeger:
	ansible-playbook -v -i inventory.yaml --diff playbooks/jaeger.yaml -l $$(hostnamectl --static)

install-grafana:
	ansible-playbook -v -i inventory.yaml --diff playbooks/grafana.yaml --vault-password-file vault/pwd.sh -l $$(hostnamectl --static)
