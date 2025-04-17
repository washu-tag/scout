resource "kubernetes_namespace" "istio_system" {
  metadata {
    name = "istio-system"
    labels = {
      "istio-injection" = "enabled"
    }
  }
}

resource "helm_release" "istio_base" {
  name       = "istio-base"
  repository = "https://istio-release.storage.googleapis.com/charts"
  chart      = "base"
  namespace  = kubernetes_namespace.istio_system.metadata[0].name
  version    = var.istio_chart_version
}

resource "helm_release" "istiod" {
  name       = "istiod"
  repository = "https://istio-release.storage.googleapis.com/charts"
  chart      = "istiod"
  namespace  = kubernetes_namespace.istio_system.metadata[0].name
  version    = var.istio_chart_version
  depends_on = [helm_release.istio_base]
  values     = [file("${path.module}/values-istio.yaml")]

  timeout          = 600
  wait             = true
  dependency_update = true
  create_namespace = true
}

resource "helm_release" "istio_ingress" {
  name       = "istio-ingress"
  repository = "https://istio-release.storage.googleapis.com/charts"
  chart      = "gateway"
  namespace  = kubernetes_namespace.istio_system.metadata[0].name
  version    = var.istio_chart_version
  depends_on = [helm_release.istiod]
}

resource "helm_release" "kiali" {
  name       = "kiali"
  repository = "https://kiali.org/helm-charts"
  chart      = "kiali-server"
  namespace  = kubernetes_namespace.istio_system.metadata[0].name
  version    = var.kiali_chart_version
  values     = [file("${path.module}/values-kiali.yaml")]
  depends_on = [helm_release.istiod]
}
resource "kubernetes_manifest" "istio_proxy_service" {
  #provider = kubernetes-alpha
  manifest = yamldecode(file("${path.module}/istio-proxy-service.yaml"))
}

resource "kubernetes_manifest" "istio_proxy_servicemonitor" {
  #provider = kubernetes-alpha
  manifest = yamldecode(file("${path.module}/istio-proxy-servicemonitor.yaml"))
}
resource "kubernetes_manifest" "argocd_istio_metrics_service" {
  #provider = kubernetes-alpha
  manifest = {
    apiVersion = "v1"
    kind       = "Service"
    metadata = {
      name      = "http-envoy-prom"
      namespace = "argocd"
      labels = {
        "app.kubernetes.io/part-of" = "argocd"
      }
    }
    spec = {
      selector = {
        "app.kubernetes.io/part-of" = "argocd"
      }
      ports = [{
        name       = "http-envoy-prom"
        port       = 15090
        targetPort = 15090
        protocol   = "TCP"
      }]
    }
  }
}
