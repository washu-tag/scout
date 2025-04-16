resource "helm_release" "karpenter" {
  name       = "karpenter"
  namespace  = "karpenter"
  repository = "https://charts.karpenter.sh"
  chart      = "karpenter"
  version    = "0.16.3"

  create_namespace = true

  values = [
    yamlencode({
      serviceAccount = {
        annotations = {
          "eks.amazonaws.com/role-arn" = var.karpenter_iam_role_arn
        }
      }
      settings = {
        clusterName = var.cluster_name
        clusterEndpoint = var.endpoint
      }
    })
  ]
}

#resource "kubernetes_manifest" "karpenter_nodepool" {
#  manifest = yamldecode(file("${path.module}/nodepool.yaml")) 
#}
