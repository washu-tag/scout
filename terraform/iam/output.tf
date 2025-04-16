output "cluster_role" {
  value = aws_iam_role.my_cluster_role.arn
}

output "cluster_nodes_role" {
  value = aws_iam_role.my_cluster_nodes_role.arn
}
output "karpenter_controller_iam_role_arn" {
  value       = try(aws_iam_role.karpenter_controller[0].arn, null)
  description = "IAM Role ARN for Karpenter controller"
  
}
