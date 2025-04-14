resource "kubernetes_namespace" "argocd" {
  metadata {
    name = "argocd"

    labels = {
      "istio-injection" = "enabled"
    }
  }
}

resource "helm_release" "argocd" {
  name       = "argocd"
  namespace  = kubernetes_namespace.argocd.metadata[0].name
  repository = "https://argoproj.github.io/argo-helm"
  chart      = "argo-cd"
  version    = "5.46.3"
  create_namespace = false
}

# resource "helm_release" "example_app" {
#   name       = "nginx"
#   namespace  = "default"
#   repository = "https://charts.bitnami.com/bitnami"
#   chart      = "nginx"
#   version    = "15.1.0"
# }

###### Enable HPA for ArgoCD ########
# resource "kubectl_manifest" "argocd_server_hpa" {
#   yaml_body = file("${path.module}/hpa-argocd-server.yaml")
# }

