resource "kubernetes_namespace" "monitoring" {
  metadata {
    name = "monitoring"
    # labels = {
    #   "istio-injection" = "enabled"
    # }
  }
}

resource "helm_release" "kube_prometheus_stack" {
  name       = "kube-prometheus-stack"
  namespace  = kubernetes_namespace.monitoring.metadata[0].name
  repository = "https://prometheus-community.github.io/helm-charts"
  chart      = "kube-prometheus-stack"
  version    = "58.6.1"

  timeout = 600
  wait    = true
  values     = [file("${path.module}/values.yaml")]


  set {
    name  = "admissionWebhooks.patch.podAnnotations.sidecar\\.istio\\.io/inject"
    value = "false"
  }
  lifecycle {
    ignore_changes = [
      values,
      set,
    ]
  }
}
