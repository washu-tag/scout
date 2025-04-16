variable "tags" {
  type = map(string)
}

variable "enable_node_autoscaling" {
  type        = bool
  description = "Enable IAM resources for Karpenter cluster autoscaling"
  default     = false
}
