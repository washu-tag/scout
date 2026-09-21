{{- define "example.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end }}

{{- define "example.fullname" -}}
{{- default (include "example.name" .) .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- end }}

{{- define "example.labels" -}}
app.kubernetes.io/name: {{ include "example.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" }}
{{- end }}

{{- define "example.selectorLabels" -}}
app.kubernetes.io/name: {{ include "example.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{- define "example.host" -}}
{{- printf "%s.%s" .Values.subdomain (required "domain is required" .Values.domain) -}}
{{- end }}

{{- define "example.clientSecretName" -}}
{{- printf "%s-keycloak-client" .Values.clientId -}}
{{- end }}
