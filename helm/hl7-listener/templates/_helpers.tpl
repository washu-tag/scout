{{/*
Chart name and version, for the helm.sh/chart label.
*/}}
{{- define "hl7-listener.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
Full metadata labels for a component.
Usage: {{ include "hl7-listener.labels" (dict "root" $ "name" "hl7-batcher" "component" "batcher") }}
app.kubernetes.io/name carries the per-component name (hl7-listener / hl7-batcher /
hl7-test-sender); the Prometheus scrape maps it to the `integration` label the dashboards
and alerts key on. app.kubernetes.io/part-of is the constant the scrape selects on.
*/}}
{{- define "hl7-listener.labels" -}}
helm.sh/chart: {{ include "hl7-listener.chart" .root }}
app.kubernetes.io/name: {{ .name }}
app.kubernetes.io/instance: {{ .root.Release.Name }}
app.kubernetes.io/part-of: hl7-listener
app.kubernetes.io/component: {{ .component }}
app.kubernetes.io/managed-by: {{ .root.Release.Service }}
{{- with .root.Chart.AppVersion }}
app.kubernetes.io/version: {{ . | quote }}
{{- end }}
{{- end -}}

{{/*
Selector labels for a component (must stay stable across upgrades).
Usage: {{ include "hl7-listener.selectorLabels" (dict "component" "batcher") }}
*/}}
{{- define "hl7-listener.selectorLabels" -}}
app.kubernetes.io/component: {{ .component }}
app.kubernetes.io/part-of: hl7-listener
{{- end -}}

{{/*
Fully-qualified image reference.
*/}}
{{- define "hl7-listener.image" -}}
{{ .Values.image.repository }}:{{ .Values.image.tag | default .Chart.AppVersion }}
{{- end -}}
