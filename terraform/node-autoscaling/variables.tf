variable "cluster_name" {
  type        = string
  description = "EKS cluster name"
}

variable "endpoint" {
  type        = string
  description = "EKS cluster endpoint"
}

variable "karpenter_iam_role_arn" {
  type        = string
  description = "IAM role ARN for Karpenter's service account"
}
