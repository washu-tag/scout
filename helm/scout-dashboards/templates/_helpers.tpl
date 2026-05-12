{{/*
Expand the name of the chart.
*/}}
{{- define "scout-dashboards.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Fully qualified app name.
*/}}
{{- define "scout-dashboards.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Chart name and version label.
*/}}
{{- define "scout-dashboards.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels.
*/}}
{{- define "scout-dashboards.labels" -}}
helm.sh/chart: {{ include "scout-dashboards.chart" . }}
{{ include "scout-dashboards.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels.
*/}}
{{- define "scout-dashboards.selectorLabels" -}}
app.kubernetes.io/name: {{ include "scout-dashboards.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Render the Scout Data Lake database YAML, baking in the Trino connection.
Used both inside the ConfigMap and (indirectly via the ConfigMap hash) by the
import Job's checksum/config annotation.
*/}}
{{- define "scout-dashboards.databaseYaml" -}}
database_name: Scout Data Lake
sqlalchemy_uri: trino://{{ .Values.trino.user }}@trino.{{ .Values.trino.namespace }}:8080/delta
cache_timeout: null
expose_in_sqllab: true
allow_run_async: false
allow_ctas: false
allow_cvas: false
allow_dml: false
allow_file_upload: false
extra:
  metadata_params: {}
  engine_params: {}
  metadata_cache_timeout: {}
  schemas_allowed_for_file_upload: []
impersonate_user: false
uuid: 9ed918e7-ef5a-4aa4-9574-f1946b58d520
version: 1.0.0
{{- end -}}

{{/*
Render the configmap items list — one entry per enabled analytics file,
mapping the configmap key (file basename) to the mount path Superset's
import-dashboards command expects (analytics/<kind>/<file>, with the family
subdir dropped from the mount path). Iterates over .Values.dashboards so
families can be toggled without touching templates.
*/}}
{{- define "scout-dashboards.configMapItems" -}}
{{- range $family, $cfg := .Values.dashboards }}
{{- if $cfg.enabled }}
{{- range $path, $_ := $.Files.Glob (printf "files/analytics/charts/%s/*.yaml" $family) }}
- key: {{ base $path }}
  path: analytics/charts/{{ base $path }}
{{- end }}
{{- range $path, $_ := $.Files.Glob (printf "files/analytics/dashboards/%s/*.yaml" $family) }}
- key: {{ base $path }}
  path: analytics/dashboards/{{ base $path }}
{{- end }}
{{- range $path, $_ := $.Files.Glob (printf "files/analytics/datasets/Scout_Data_Lake/%s/*.yaml" $family) }}
- key: {{ base $path }}
  path: analytics/datasets/Scout_Data_Lake/{{ base $path }}
{{- end }}
{{- end }}
{{- end }}
- key: metadata.yaml
  path: analytics/metadata.yaml
- key: Scout_Data_Lake.yaml
  path: analytics/databases/Scout_Data_Lake.yaml
- key: import.py
  path: import.py
{{- end -}}
