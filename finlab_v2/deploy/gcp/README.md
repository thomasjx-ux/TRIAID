# TRIAID FIN V2 Google Cloud deployment

This target is deliberately independent from Railway.

Architecture:

- Cloud Run: stateless UI and API
- Supabase: persistent TRIAID state, runs, observations, transitions, scheduler state and audit state
- Cloud Scheduler: one weekday minute tick
- Secret Manager: runtime secrets
- Railway: remains online as the existing production/backup node during migration

The Cloud Run service runs with:
- min instances: 0
- max instances: 1
- concurrency: 1
- request-based execution
- in-process market loop disabled
- external scheduler tick enabled
- broker execution unchanged and disabled

The scheduler calls:

POST /api/market-data/automation/tick

The tick uses the same official calendar and frequency policy as the Railway runtime. It persists last refresh timestamps and market phases, so a Cloud Run cold start does not reset cadence or create duplicate refreshes.

Calendar maintenance is folded into the same tick. Its existing six-hour interval guard prevents unnecessary exchange-calendar downloads.

## Required Secret Manager secrets

Create exactly these three secrets before running deploy.sh:

- triaid-admin-token
- triaid-supabase-url
- triaid-supabase-token

Do not commit secret values to GitHub.

## Deploy

From the repository root in Google Cloud Shell:

bash finlab_v2/deploy/gcp/deploy.sh YOUR_PROJECT_ID

The script:
1. enables the required Google APIs
2. creates a least-purpose Cloud Run runtime service account
3. verifies the three secrets and grants runtime access
4. deploys finlab_v2 from source
5. sets Cloud Run max instances and concurrency to one to protect scheduler idempotence
6. creates or updates one weekday Cloud Scheduler job at one-minute cadence
7. runs health, status and one live scheduler tick
8. fails unless persistent Supabase storage and the scheduler tick are healthy

## Migration rule

Do not stop Railway when this deploy succeeds.

Promotion requires a dual-run comparison first:
- same source timestamps
- same market-data provider identity
- same observation/transition semantics
- same decision events for equivalent inputs
- persistent state integrity
- repeated cold-start cadence test
- US and CN shadow-run comparison
- no duplicate decision events

Only after those checks should Google Cloud become primary.
