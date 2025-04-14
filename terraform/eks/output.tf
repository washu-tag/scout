output "endpoint" {
  value = aws_eks_cluster.my_cluster.endpoint
}

output "kubeconfig-certificate-authority-data" {
  value = aws_eks_cluster.my_cluster.certificate_authority[0].data
}

output "cluster_name" {
  value = aws_eks_cluster.my_cluster.name
}

output "cluster_token" {
  value = data.aws_eks_cluster_auth.auth.token
}

