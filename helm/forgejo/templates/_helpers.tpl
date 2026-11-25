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

{{/* Pods that call the Forgejo API as its admin, from inside the namespace. */}}
{{- define "forgejo.apiClientPod" -}}
automountServiceAccountToken: false
securityContext:
  runAsNonRoot: true
  runAsUser: 100
  seccompProfile: { type: RuntimeDefault }
{{- end -}}

{{- define "forgejo.apiClientEnv" -}}
- name: FORGEJO_URL
  value: http://{{ include "forgejo.serviceName" . }}:3000
- name: ADMIN_USER
  valueFrom:
    secretKeyRef: { name: {{ printf "%s-admin" .Values.upstream.fullnameOverride }}, key: username }
- name: ADMIN_PASSWORD
  valueFrom:
    secretKeyRef: { name: {{ printf "%s-admin" .Values.upstream.fullnameOverride }}, key: password }
{{- end -}}

{{- define "forgejo.apiClientContainer" -}}
resources:
  requests: { cpu: 5m, memory: 16Mi }
  limits: { memory: 64Mi }
securityContext:
  allowPrivilegeEscalation: false
  readOnlyRootFilesystem: true
  capabilities: { drop: [ALL] }
{{- end -}}
