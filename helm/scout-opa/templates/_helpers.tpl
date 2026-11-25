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

{{/* Effective policy files: the value when set, else the chart's own file. */}}
{{- define "scout-opa.rego" -}}
{{- .Values.policy.rego | default (.Files.Get "files/main.rego") -}}
{{- end }}
{{- define "scout-opa.authz" -}}
{{- .Values.policy.authz | default (.Files.Get "files/authz.rego") -}}
{{- end }}
{{- define "scout-opa.scout" -}}
{{- .Values.policy.scout | default (.Files.Get "files/scout.rego") -}}
{{- end }}

{{/* Changes whenever the rules deciding query results change (the Trino
     policy or its static data); published as data.policy_revision. */}}
{{- define "scout-opa.policyRevision" -}}
{{- printf "%s%s" (include "scout-opa.rego" .) .Values.data.json | sha256sum | trunc 16 -}}
{{- end }}

{{/* Rollout hash of every policy file + data + config + bundle-reader credsHash,
     so an in-place edit to a chart file still rolls pods at the fixed dev chart
     version. */}}
{{- define "scout-opa.policyHash" -}}
{{- printf "%s%s%s%s%s%s" (include "scout-opa.rego" .) (include "scout-opa.authz" .) (include "scout-opa.scout" .) .Values.data.json .Values.config.yaml .Values.bundleReader.credsHash | sha256sum | trunc 8 -}}
{{- end }}
