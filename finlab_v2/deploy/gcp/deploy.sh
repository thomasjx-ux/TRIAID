#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID="${1:-${GOOGLE_CLOUD_PROJECT:-}}"
REGION="${REGION:-us-central1}"
SCHEDULER_REGION="${SCHEDULER_REGION:-us-central1}"
SERVICE="${SERVICE:-triaid-fin-v2}"
RUNTIME_SA_NAME="${RUNTIME_SA_NAME:-triaid-fin-runtime}"
SCHEDULER_JOB="${SCHEDULER_JOB:-triaid-fin-market-tick}"
SOURCE_DIR="${SOURCE_DIR:-finlab_v2}"
STORAGE_NAMESPACE="${STORAGE_NAMESPACE:-gcp-shadow}"

if [[ -z "${PROJECT_ID}" ]]; then
  echo "Usage: $0 <google-cloud-project-id>"
  exit 2
fi

for cmd in gcloud curl; do
  command -v "${cmd}" >/dev/null 2>&1 || { echo "missing required command: ${cmd}"; exit 2; }
done

gcloud config set project "${PROJECT_ID}" >/dev/null

gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  secretmanager.googleapis.com \
  cloudscheduler.googleapis.com

PROJECT_NUMBER="$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)')"
RUNTIME_SA="${RUNTIME_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

if ! gcloud iam service-accounts describe "${RUNTIME_SA}" >/dev/null 2>&1; then
  gcloud iam service-accounts create "${RUNTIME_SA_NAME}" \
    --display-name="TRIAID FIN Cloud Run runtime"
fi

required_secrets=(
  triaid-admin-token
  triaid-supabase-url
  triaid-supabase-token
)

for secret in "${required_secrets[@]}"; do
  if ! gcloud secrets describe "${secret}" >/dev/null 2>&1; then
    echo "missing Secret Manager secret: ${secret}"
    echo "Create it in Secret Manager before running this script again."
    exit 3
  fi
  gcloud secrets add-iam-policy-binding "${secret}" \
    --member="serviceAccount:${RUNTIME_SA}" \
    --role="roles/secretmanager.secretAccessor" >/dev/null
done

latest_enabled_version() {
  gcloud secrets versions list "$1" \
    --filter='state=ENABLED' \
    --sort-by='~createTime' \
    --limit=1 \
    --format='value(name)' | sed 's#.*/##'
}

ADMIN_VERSION="$(latest_enabled_version triaid-admin-token)"
SUPABASE_URL_VERSION="$(latest_enabled_version triaid-supabase-url)"
SUPABASE_TOKEN_VERSION="$(latest_enabled_version triaid-supabase-token)"

for item in ADMIN_VERSION SUPABASE_URL_VERSION SUPABASE_TOKEN_VERSION; do
  if [[ -z "${!item}" ]]; then
    echo "no enabled secret version found for ${item}"
    exit 3
  fi
done

gcloud run deploy "${SERVICE}" \
  --source "${SOURCE_DIR}" \
  --region "${REGION}" \
  --platform managed \
  --allow-unauthenticated \
  --service-account "${RUNTIME_SA}" \
  --cpu 1 \
  --memory 512Mi \
  --concurrency 1 \
  --min-instances 0 \
  --max-instances 1 \
  --timeout 300 \
  --set-env-vars "PYTHONUNBUFFERED=1,TRIAID_STORAGE_BACKEND=supabase,TRIAID_STORAGE_NAMESPACE=${STORAGE_NAMESPACE},TRIAID_DATA_AUTOMATION=0,TRIAID_CALENDAR_SYNC=0,TRIAID_DECISION_AUTOMATION=1,ALPACA_DATA_FEED=iex,TRIAID_DEPLOY_TARGET=GCP_CLOUD_RUN" \
  --set-secrets "TRIAID_ADMIN_TOKEN=triaid-admin-token:${ADMIN_VERSION},TRIAID_SUPABASE_PERSISTENCE_URL=triaid-supabase-url:${SUPABASE_URL_VERSION},TRIAID_SUPABASE_TOKEN=triaid-supabase-token:${SUPABASE_TOKEN_VERSION}"

SERVICE_URL="$(gcloud run services describe "${SERVICE}" --region "${REGION}" --format='value(status.url)')"
if [[ -z "${SERVICE_URL}" ]]; then
  echo "Cloud Run service URL not found"
  exit 4
fi

ADMIN_TOKEN="$(gcloud secrets versions access "${ADMIN_VERSION}" --secret=triaid-admin-token)"

scheduler_args=(
  --location "${SCHEDULER_REGION}"
  --schedule "* * * * 1-5"
  --time-zone "Etc/UTC"
  --uri "${SERVICE_URL}/api/market-data/automation/tick"
  --http-method POST
  --headers "X-TRIAID-ADMIN-TOKEN=${ADMIN_TOKEN}"
  --attempt-deadline 300s
)

if gcloud scheduler jobs describe "${SCHEDULER_JOB}" --location "${SCHEDULER_REGION}" >/dev/null 2>&1; then
  gcloud scheduler jobs update http "${SCHEDULER_JOB}" "${scheduler_args[@]}"
else
  gcloud scheduler jobs create http "${SCHEDULER_JOB}" "${scheduler_args[@]}"
fi

echo "Cloud Run URL: ${SERVICE_URL}"
echo "Running post-deploy checks..."

curl -fsS "${SERVICE_URL}/health" >/tmp/triaid-gcp-health.json
curl -fsS "${SERVICE_URL}/api/status" >/tmp/triaid-gcp-status.json
curl -fsS -X POST \
  -H "X-TRIAID-ADMIN-TOKEN: ${ADMIN_TOKEN}" \
  "${SERVICE_URL}/api/market-data/automation/tick" >/tmp/triaid-gcp-tick.json

python - <<'PY'
import json
from pathlib import Path

health=json.loads(Path("/tmp/triaid-gcp-health.json").read_text())
status=json.loads(Path("/tmp/triaid-gcp-status.json").read_text())
tick=json.loads(Path("/tmp/triaid-gcp-tick.json").read_text())

assert health.get("ok") is True, health
storage=status.get("storage") or {}
assert storage.get("durability")=="PERSISTENT", storage
backend=(storage.get("backend") or {}).get("backend")
assert backend=="supabase", storage
assert tick.get("ok") is True, tick

print("TRIAID_GCP_POSTDEPLOY_PASS")
print("storage_backend", backend)
print("tick_refreshed", len(tick.get("refreshed") or []))
print("tick_skipped", len(tick.get("skipped") or []))
PY
