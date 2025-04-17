variable "tags" {
  type = map(string)
  default = {
    "user" = "washu",
    "name"  = "eks-demo"
  }
}

variable "cluster_name" {
  type    = string
  default = "eks-demo"
}

variable "enable_argocd" {
  type    = bool
  default = true
}

variable "enable_monitoring" {
  type    = bool
  default = true
}

variable "enable_istio" {
  type    = bool
  default = false
}

variable "enable_node_autoscaling" {
  description = "Enable cluster's autoscaling module (Karpenter)"
  type        = bool
  default     = false
}

