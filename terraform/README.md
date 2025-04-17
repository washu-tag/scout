# eks-terraform

Terraform configuration for deploying an EKS cluster with supporting components.

## Prerequisites

- [Terraform](https://www.terraform.io/)
- [AWS CLI](https://docs.aws.amazon.com/cli/latest/userguide/install-cliv2.html)
- [kubectl](https://kubernetes.io/docs/tasks/tools/)

## Usage

```bash
cd terraform
terraform init
terraform plan
terraform apply
```

## Variables

All variables are defined in `terraform/variables.tf`. You can override them using a `.tfvars` file or directly with `-var` flags.

Example:

```hcl
# example.tfvars

region        = "us-east-1"
cluster_name  = "my-cluster"
```

Then apply with:

```bash
terraform apply -var-file="example.tfvars"
```

## Structure

The project is modularized under `terraform/` with submodules for:

- `eks`: EKS cluster and node groups
- `network`: VPC, subnets, and networking components
- `iam`: Roles and policies for EKS and other services
- `argocd`: GitOps deployment (optional)
- `istio`: Service mesh configuration, and Kiali (optional)
- `monitoring`: Prometheus, Grafana (optional)
- `node-autoscaling`: Karpenter and HPA setup (optional)
