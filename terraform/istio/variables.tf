variable "istio_chart_version" {
  description = "Version of Istio Helm charts"
  type        = string
  default     = "1.25.2"
}

variable "kiali_chart_version" {
  description = "Version of Kiali chart"
  type        = string
  default     = "2.8.0"
}
variable "enable_istio" {
  description = "Enable Istio installation"
  type        = bool
  default     = false
}

variable "istio_injection_namespaces" {
  description = "List of namespaces to enable istio-injection"
  type        = list(string)
  default     = ["monitoring", "argocd"]
}
variable "cluster_endpoint" {
  description = "Cluster endpoint for Kubernetes provider"
  type        = string
}

variable "cluster_ca_certificate" {
  description = "Base64 encoded certificate authority data"
  type        = string
}

variable "cluster_token" {
  description = "Bearer token to authenticate against the cluster"
  type        = string
}
