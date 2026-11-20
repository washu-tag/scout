{{- define "forgejo.host" -}}
{{- required "domain is required" .Values.domain | printf "%s.%s" .Values.subdomain -}}
{{- end -}}

{{- define "forgejo.labels" -}}
app.kubernetes.io/name: forgejo
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version | replace "+" "_" }}
{{- end -}}

{{- define "forgejo.serviceName" -}}
{{- printf "%s-http" .Values.upstream.fullnameOverride -}}
{{- end -}}
