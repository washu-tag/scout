{{/* Fixed name; single OPA per cluster, no per-release suffix. */}}
{{- define "scout-opa.fullname" -}}
opa-trino
{{- end }}

{{- define "scout-opa.labels" -}}
app: {{ include "scout-opa.fullname" . }}
app.kubernetes.io/name: {{ include "scout-opa.fullname" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{- define "scout-opa.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" -}}
{{- end }}

{{/* Object-metadata labels: scout-opa.labels plus helm.sh/chart. Not used on the
     pod template — keeping the chart version out of spec.template means a release
     bump leaves a manifest diff for helm to upgrade on (so the release reports its
     real chart version) without rolling pods. Rollout is driven by policyHash. */}}
{{- define "scout-opa.objectLabels" -}}
helm.sh/chart: {{ include "scout-opa.chart" . }}
{{ include "scout-opa.labels" . }}
{{- end }}

{{- define "scout-opa.selectorLabels" -}}
app: {{ include "scout-opa.fullname" . }}
{{- end }}

{{/* SA name: default = the fixed fullname (opa-trino) when created, else `default`. */}}
{{- define "scout-opa.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (include "scout-opa.fullname" .) .Values.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.serviceAccount.name -}}
{{- end -}}
{{- end }}

{{/* Rollout hash of policy + data + config + bundle-reader credsHash. Hashes the
     EFFECTIVE rego (chart file when no override) so an in-place edit to
     files/main.rego still rolls pods at the fixed dev chart version. */}}
{{- define "scout-opa.policyHash" -}}
{{- $rego := .Values.policy.rego | default (.Files.Get "files/main.rego") -}}
{{- printf "%s%s%s%s" $rego .Values.data.json .Values.config.yaml .Values.bundleReader.credsHash | sha256sum | trunc 8 -}}
{{- end }}
