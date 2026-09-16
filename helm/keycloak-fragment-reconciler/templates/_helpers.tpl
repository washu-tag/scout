{{- define "reconciler.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end }}

{{- define "reconciler.fullname" -}}
{{- default (include "reconciler.name" .) .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- end }}

{{- define "reconciler.labels" -}}
app.kubernetes.io/name: {{ include "reconciler.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" }}
{{- end }}

{{- define "reconciler.selectorLabels" -}}
app.kubernetes.io/name: {{ include "reconciler.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Not derived from the release name, unlike every other name here: apps hardcode
this string in a RoleBinding, so it must be the same on every site.
*/}}
{{- define "reconciler.serviceAccountName" -}}
{{- required "serviceAccount.name is required" .Values.serviceAccount.name -}}
{{- end }}

{{/*
Cluster-scoped names carry the release namespace, or two releases in one cluster
fight over a single ClusterRole/Binding.
*/}}
{{- define "reconciler.clusterName" -}}
{{- printf "%s-%s" (include "reconciler.fullname" .) .Release.Namespace | trunc 63 | trimSuffix "-" -}}
{{- end }}
