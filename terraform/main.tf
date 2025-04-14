module "network" {
  source = "./network"
  tags   = var.tags
}

module "iam" {
  source = "./iam"
  tags   = var.tags
}

module "eks" {
  source             = "./eks"
  cluster_name       = var.cluster_name
  cluster_subnets    = module.network.subnets_ids
  cluster_role       = module.iam.cluster_role
  cluster_nodes_role = module.iam.cluster_nodes_role
  tags               = var.tags
}

provider "kubernetes" {
  host                   = module.eks.endpoint
  cluster_ca_certificate = base64decode(module.eks.kubeconfig-certificate-authority-data)
  token                  = module.eks.cluster_token
}

provider "helm" {
  kubernetes {
    host                   = module.eks.endpoint
    cluster_ca_certificate = base64decode(module.eks.kubeconfig-certificate-authority-data)
    token                  = module.eks.cluster_token
  }
}

module "argocd" {
  source = "./argocd"
  count  = var.enable_argocd ? 1 : 0
  cluster_endpoint         = module.eks.endpoint
  cluster_ca_certificate   = module.eks.kubeconfig-certificate-authority-data
  cluster_token            = module.eks.cluster_token
  providers = {
    helm       = helm
    kubernetes = kubernetes
  }
}

module "monitoring" {
  source = "./monitoring"
  count  = var.enable_monitoring ? 1 : 0
  cluster_endpoint         = module.eks.endpoint
  cluster_ca_certificate   = module.eks.kubeconfig-certificate-authority-data
  cluster_token            = module.eks.cluster_token
  providers = {
    helm       = helm
    kubernetes = kubernetes
  }
}

module "istio" {
  source = "./istio"
  count  = var.enable_istio ? 1 : 0
  cluster_token          = module.eks.cluster_token
  cluster_endpoint       = module.eks.endpoint
  cluster_ca_certificate = module.eks.kubeconfig-certificate-authority-data
  providers = {
    helm       = helm
    kubernetes = kubernetes
    #kubernetes-alpha = kubernetes-alpha
  }
}

module "node_autoscaling" {
  source = "./node-autoscaling"
  count  = var.enable_node_autoscaling ? 1 : 0
  cluster_name          = module.eks.cluster_name
  endpoint      = module.eks.endpoint
  karpenter_iam_role_arn = module.iam.karpenter_controller_iam_role_arn
}

