{{/*
Expand the name of the chart.
*/}}
{{- define "hl7-transformer.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
We truncate at 63 chars because some Kubernetes name fields are limited to this (by the DNS naming spec).
If release name contains chart name it will be used as a full name.
*/}}
{{- define "hl7-transformer.fullname" -}}
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
Create chart name and version as used by the chart label.
*/}}
{{- define "hl7-transformer.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "hl7-transformer.labels" -}}
helm.sh/chart: {{ include "hl7-transformer.chart" . }}
{{ include "hl7-transformer.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "hl7-transformer.selectorLabels" -}}
app.kubernetes.io/name: {{ include "hl7-transformer.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Create the name of the service account to use
*/}}
{{- define "hl7-transformer.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "hl7-transformer.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{/*
Render spark-defaults.conf from the sparkDefaults values block. This is the
former scout_common/spark-defaults.conf.j2, moved into the chart (ADR 0031 §4)
and switched on .Values.sparkDefaults.mode (the ADR 0011 service mode).

On-prem reads its S3 credentials from the AWS_ACCESS_KEY_ID /
AWS_SECRET_ACCESS_KEY environment variables (the s3-secret the Deployment
already mounts via envFrom) through the SDK-v2 EnvironmentVariableCredentialsProvider,
rather than inlining the keys here, so no credentials live in the ConfigMap.
*/}}
{{- define "hl7-transformer.sparkDefaultsConf" -}}
{{- $s := .Values.sparkDefaults -}}
{{- if not (has $s.mode (list "on-prem" "aws")) -}}
{{- fail (printf "sparkDefaults.mode must be \"on-prem\" or \"aws\", got %q" $s.mode) -}}
{{- end -}}
{{- $aws := eq $s.mode "aws" -}}
{{- if $aws }}
spark.hadoop.fs.s3a.aws.credentials.provider software.amazon.awssdk.auth.credentials.WebIdentityTokenFileCredentialsProvider
{{- else }}
spark.hadoop.fs.s3a.endpoint {{ required "sparkDefaults.s3Endpoint is required when sparkDefaults.mode=on-prem" $s.s3Endpoint }}
spark.hadoop.fs.s3a.aws.credentials.provider software.amazon.awssdk.auth.credentials.EnvironmentVariableCredentialsProvider
{{- end }}
spark.hadoop.fs.s3a.endpoint.region {{ $s.s3Region }}
# Spark 4 enables ANSI mode by default; the HL7 extraction relies on
# out-of-range array access and unparseable timestamps yielding NULL.
spark.sql.ansi.enabled false
spark.databricks.delta.schema.autoMerge.enabled true
spark.databricks.delta.merge.repartitionBeforeWrite.enabled true
spark.databricks.delta.constraints.allowUnenforcedNotNull.enabled true
spark.sql.extensions io.delta.sql.DeltaSparkSessionExtension
spark.sql.catalog.spark_catalog org.apache.spark.sql.delta.catalog.DeltaCatalog
spark.hadoop.fs.s3a.path.style.access {{ if $aws }}false{{ else }}true{{ end }}
spark.hadoop.hive.metastore.uris {{ required "sparkDefaults.hiveMetastoreUri is required when sparkDefaults is enabled" $s.hiveMetastoreUri }}
spark.sql.warehouse.dir {{ required "sparkDefaults.warehouseDir is required when sparkDefaults is enabled" $s.warehouseDir }}
spark.sql.shuffle.partitions {{ $s.shufflePartitions }}
spark.driver.extraJavaOptions -Divy.cache.dir=/tmp -Divy.home=/tmp
spark.executor.memory {{ .Values.spark.executor.memory }}
spark.driver.memory {{ .Values.spark.executor.memory }}
{{- end }}
