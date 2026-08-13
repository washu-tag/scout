{{/* Fixed name; Scout deploys a single OPA per cluster, no per-release suffix needed. */}}
{{- define "scout-opa.fullname" -}}
opa-trino
{{- end }}

{{- define "scout-opa.labels" -}}
app: {{ include "scout-opa.fullname" . }}
app.kubernetes.io/name: {{ include "scout-opa.fullname" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{- define "scout-opa.selectorLabels" -}}
app: {{ include "scout-opa.fullname" . }}
{{- end }}

{{/* Hash of all content the Deployment must pick up via pod restart:
     policy file, static data document, bundle plugin config, and the
     bundle-reader credentials hash supplied by the Ansible role. Hashes the
     EFFECTIVE rego (chart file when no override) so an in-place edit to
     files/main.rego still rolls pods at the fixed dev chart version. */}}
{{- define "scout-opa.policyHash" -}}
{{- $rego := .Values.policy.rego | default (.Files.Get "files/main.rego") -}}
{{- printf "%s%s%s%s" $rego .Values.data.json .Values.config.yaml .Values.bundleReader.credsHash | sha256sum | trunc 8 -}}
{{- end }}
