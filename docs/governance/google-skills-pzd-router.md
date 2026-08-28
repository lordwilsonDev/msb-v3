# Google Skills PZD Router

**Source:** `https://github.com/google/skills.git`
**Installed:** 2026-08-28
**Purpose:** Select the appropriate Google skill before acting on Google Cloud,
Gemini, Agent Platform, Google API, or Google developer-documentation work.

## Routing contract

1. Match the most specific route first.
2. Use exactly one primary Google skill unless the task explicitly spans
   multiple products.
3. Use `gcloud` before constructing or executing any `gcloud` command.
4. Use `retrieving-developer-knowledge` when the request is documentation,
   API syntax, IAM permission, or official Google-product research.
5. Do not route local MSB-v3, local speech/audio, filesystem, or non-Google API
   work to these skills.
6. For destructive cloud operations, pause for explicit approval before acting.

## Routes

| Priority | Intent signals | Skill | Do not use when |
|---:|---|---|---|
| 1 | `gcloud`, CLI command, `gcloud` syntax, flags, execute/plan a GCP CLI operation | `gcloud` | Writing client-library or raw REST code without CLI work |
| 2 | Search official Google developer docs, API syntax, IAM permissions, product comparison | `retrieving-developer-knowledge` | Local filesystem lookup or non-Google documentation |
| 3 | Gemini API, Vertex AI, Agent Platform inference, prompt/inference request, Gemini model call | `agent-platform-inference` for Agent Platform/Vertex; `gemini-api` for explicit Gemini API usage | Deployment, endpoint lifecycle, or evaluation is the primary task |
| 4 | Create/run/debug/evaluate/deploy an ADK or agents-cli agent lifecycle | `google-agents-cli-onboarding` | Managing server-side Agent resources directly |
| 5 | Evaluate an agent/model, create eval datasets, metrics, judge scores, compare versions | `agent-platform-eval-flywheel` | Production deployment or endpoint administration |
| 6 | Deploy/undeploy model, Model Garden, serving endpoint deployment operation | `agent-platform-deploy` | Endpoint CRUD without model deployment; use endpoint management |
| 7 | Create/list/update/delete Agent Platform serving endpoints | `agent-platform-endpoint-management` | Deploying a model to an endpoint |
| 8 | Explicit Google Cloud / Vertex / Agent Platform architecture involving multiple products | `google-cloud-solution-architecture` or the narrower product skill | A narrow product task already covered by a route above |

## Secondary routes from the Google catalog

Install/use only when the task actually requires the product:

- `google-ads-api-quickstart` — Google Ads API credentials, quickstarts, or
  developer-token errors.
- `google-ads-api-mcp-setup` — connect an assistant to Google Ads via the
  official MCP server.
- `google-analytics-admin-api-basics` — Analytics property/admin settings.
- `google-analytics-data-api-basics` — Analytics reporting queries.
- `bigquery-basics` — BigQuery datasets, tables, jobs, or SQL execution.
- `cloud-sql-basics` — Cloud SQL administration.
- `google-cloud-storage-basics` — GCS object/bucket operations.
- `firebase-basics` — Firebase CLI login, project selection, or app config.
- `gke-*` — only when the task explicitly concerns Google Kubernetes Engine.

## Negative routing rules

Do **not** select a Google skill for:

- MSB-v3 core, Meta-System, PZD, local Python, local tests, or repository
  refactors.
- Local speech/audio adapters, macOS `say`, PyAudio, playback, or hardware.
- Generic cloud providers or services not operated through Google Cloud.
- A third-party integration before researching the provider through the
  project's required service-discovery process.
- Local `curl`, `pytest`, `ruff`, `mypy`, Docker, or launchd operations.

## PZD decision record

```text
INPUT: user task
  ↓
GOOGLE PRODUCT OR GOOGLE CLOUD NAMED?
  ├─ no → use normal project/domain routing; do not load this pack
  └─ yes
       ↓
EXPLICIT CLI COMMAND?
  ├─ yes → gcloud
  └─ no
       ↓
DOCS/API/IAM RESEARCH?
  ├─ yes → retrieving-developer-knowledge
  └─ no
       ↓
PRIMARY OPERATION
  ├─ inference → agent-platform-inference or gemini-api
  ├─ agent lifecycle → google-agents-cli-onboarding
  ├─ evaluation → agent-platform-eval-flywheel
  ├─ model deployment → agent-platform-deploy
  ├─ endpoint management → agent-platform-endpoint-management
  └─ otherwise → select the narrowest product skill or ask for clarification
```

## Installed skills

- `agent-platform-eval-flywheel`
- `agent-platform-inference`
- `gcloud`
- `gemini-api`
- `google-agents-cli-onboarding`
- `retrieving-developer-knowledge`

Installed globally under `~/.agents/skills/`. The installer reported that its
PromptScript adapter does not support global installation; universal skill
copies were installed successfully.
