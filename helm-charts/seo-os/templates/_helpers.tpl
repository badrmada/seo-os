{{/*
Expand the name of the chart.
*/}}
{{- define "seo-os.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name, truncated to the 63 chars a DNS label
allows.
*/}}
{{- define "seo-os.fullname" -}}
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

{{- define "seo-os.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "seo-os.labels" -}}
helm.sh/chart: {{ include "seo-os.chart" . }}
{{ include "seo-os.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
seo-os.io/tenant: {{ .Values.tenant.name | quote }}
{{- end }}

{{- define "seo-os.selectorLabels" -}}
app.kubernetes.io/name: {{ include "seo-os.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
The Secret holding the tenant folder: one you already manage, or the one this
chart renders.
*/}}
{{- define "seo-os.tenantSecretName" -}}
{{- if .Values.tenant.secret.existing }}
{{- .Values.tenant.secret.existing }}
{{- else }}
{{- printf "%s-%s" (include "seo-os.fullname" .) .Values.tenant.name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}

{{/*
A Job's spec is immutable, so the release revision is part of its name: every
`helm upgrade --install` is a new run rather than a failed patch. Helm removes
the previous revision's Job as it goes — see README.md#running-it-again.
*/}}
{{- define "seo-os.jobName" -}}
{{- printf "%s-%s" (include "seo-os.fullname" .) .Values.tenant.name | trunc 55 | trimSuffix "-" }}-r{{ .Release.Revision }}
{{- end }}

{{/*
The paths this chart projects into the tenant folder, in the order they appear
in the Secret. Used twice — as the Secret's volume `items` and, in `files` mount
mode, as one `subPath` mount each — so the two can't drift apart.

Returns a newline-separated list, because a template can only return a string.
*/}}
{{- define "seo-os.tenantPaths" -}}
{{- if .Values.tenant.secret.items }}
{{- range .Values.tenant.secret.items }}
{{- .path }}{{ "\n" }}
{{- end }}
{{- else }}
{{- if or .Values.tenant.config .Values.tenant.configJson }}tenant.json{{ "\n" }}{{ end }}
{{- if or .Values.tenant.input .Values.tenant.inputJson }}input.json{{ "\n" }}{{ end }}
{{- range $path, $_ := .Values.tenant.files }}
{{- $path }}{{ "\n" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
A path inside the tenant folder to the key that carries it. A Secret key can't
contain a slash (`[-._a-zA-Z0-9]+` only) but a volume item's `path` can, which
is what lets `data/service_account.json` arrive as a file in a subdirectory.
*/}}
{{- define "seo-os.tenantSecretKey" -}}
{{- . | replace "/" "__" }}
{{- end }}
