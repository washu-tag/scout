output "istio_namespace" {
  value = kubernetes_namespace.istio_system.metadata[0].name
}
