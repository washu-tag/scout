resource "aws_eks_cluster" "my_cluster" {
  name     = var.cluster_name
  role_arn = var.cluster_role

  vpc_config {
    subnet_ids = concat(var.cluster_subnets["private_subnets"],
                        var.cluster_subnets["public_subnets"])             
  }

  #depends_on = [aws_iam_role_policy_attachment.demo-AmazonEKSClusterPolicy]

  tags = var.tags
}

resource "aws_eks_node_group" "my_private_nodes" {
  cluster_name    = aws_eks_cluster.my_cluster.name
  node_group_name = "small-ng"
  node_role_arn   = var.cluster_nodes_role

  subnet_ids = var.cluster_subnets["private_subnets"]

  capacity_type  = "ON_DEMAND"
  instance_types = ["t3.medium"]

  scaling_config {
    desired_size = 2
    max_size     = 5
    min_size     = 1
  }

  update_config {
    max_unavailable = 1
  }

  labels = {
    role = "general"
  }
}

data "aws_eks_cluster_auth" "auth" {
  name = aws_eks_cluster.my_cluster.name
}
