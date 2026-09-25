{{- define "keycloak-fragment-reconciler.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end }}

{{- define "keycloak-fragment-reconciler.fullname" -}}
{{- default (include "keycloak-fragment-reconciler.name" .) .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- end }}

{{- define "keycloak-fragment-reconciler.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" -}}
{{- end }}

{{- define "keycloak-fragment-reconciler.labels" -}}
helm.sh/chart: {{ include "keycloak-fragment-reconciler.chart" . }}
{{ include "keycloak-fragment-reconciler.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{- define "keycloak-fragment-reconciler.selectorLabels" -}}
app.kubernetes.io/name: {{ include "keycloak-fragment-reconciler.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Not derived from the release name, unlike every other name here: apps hardcode
this string in a RoleBinding, so it must be the same on every site.
*/}}
{{- define "keycloak-fragment-reconciler.serviceAccountName" -}}
{{- required "serviceAccount.name is required" .Values.serviceAccount.name -}}
{{- end }}

{{/*
Cluster-scoped names carry the release namespace, or two releases in one cluster
fight over a single ClusterRole/Binding.
*/}}
{{- define "keycloak-fragment-reconciler.clusterName" -}}
{{- printf "%s-%s" (include "keycloak-fragment-reconciler.fullname" .) .Release.Namespace | trunc 63 | trimSuffix "-" -}}
{{- end }}
