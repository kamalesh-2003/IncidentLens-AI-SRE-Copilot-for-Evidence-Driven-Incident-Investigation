# IncidentLens

Autonomous AI SRE copilot for evidence-driven incident investigation.

When a production alert fires, IncidentLens correlates recent deploys with the
metric degradation, retrieves the matching runbook, estimates user impact,
posts a Slack brief, and writes a postmortem when the alert resolves. It is
**read-only** — it diagnoses and recommends, never rolls back or executes
changes.

## Quickstart

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
uvicorn main:app --reload --port 8000
```

Fire a mock alert:

```powershell
curl.exe -X POST http://localhost:8000/webhook/alert `
  -H "Content-Type: application/json" `
  -d '{\"alert_name\":\"HighErrorRate\",\"severity\":\"critical\",\"service\":\"checkout\",\"started_at\":\"2026-07-03T14:12:00Z\"}'
```

You should see the alert logged and receive `{"received": true, ...}`. When the
same alert is re-sent with `"status": "resolved"` (and a `resolved_at`), the
workflow writes a Markdown postmortem to `postmortems/` instead.

## Architecture

```
webhook  →  orchestrator (LangGraph)
              commit_analyzer   (git log/diff + Claude tool use)  ┐
              runbook_retriever (embeddings over runbooks/)       ├─ diagnostics
              impact_estimator  (metrics store)                   ┘
                    │
                    └─ route on alert status
                         ├─ firing   → slack_poster   (Block Kit → Slack webhook)
                         └─ resolved → postmortem_gen  (→ Markdown in postmortems/)
```

The orchestrator threads one shared incident state through the stages, so the
diagnostic findings are computed once and passed to the terminal stage. Every
stage is **degrade-gracefully**: with no `ANTHROPIC_API_KEY` the commit analysis
is skipped and the postmortem uses a deterministic template; with no
`SLACK_WEBHOOK_URL` the brief prints to stdout; with no `PROMETHEUS_URL` a mock
metrics store is used; and runbook retrieval falls back to lexical matching. So
the whole pipeline runs end-to-end with zero configuration.

## Try the stages

Each stage has a runnable demo (all work keyless):

```powershell
python -m demo.run_orchestrator      # full workflow: firing brief + resolved postmortem
python -m demo.run_commit_analyzer   # needs ANTHROPIC_API_KEY
python -m demo.run_runbook_retriever
python -m demo.run_impact_estimator
python -m demo.run_slack_poster
python -m demo.run_postmortem_gen
```

## Environment

Copy `.env.example` to `.env` and fill in:

| Variable            | Required | Purpose                                              | Where to get it |
|---------------------|----------|------------------------------------------------------|-----------------|
| `ANTHROPIC_API_KEY` | recommended | Claude reasoning (commit correlation, postmortem narrative). Without it those stages skip / use templates. | https://console.anthropic.com/settings/keys |
| `SLACK_WEBHOOK_URL` | no       | Send briefs to Slack (else prints Block Kit JSON)    | https://api.slack.com/messaging/webhooks |
| `DEMO_GIT_REPO_PATH`| no       | Repo the commit analyzer inspects                    | defaults to `./demo/app` |
| `RUNBOOKS_DIR`      | no       | Directory of Markdown runbooks to search             | defaults to `./runbooks` |
| `POSTMORTEMS_DIR`   | no       | Where resolved-incident postmortems are written      | defaults to `./postmortems` |
| `VOYAGE_API_KEY`    | no       | High-quality embeddings for runbook RAG              | https://www.voyageai.com/ |
| `PROMETHEUS_URL`    | no       | Real metrics source (else uses a mock store)         | your Prometheus server |
| `LOG_LEVEL`         | no       | Root log level (defaults to `INFO`)                  | `DEBUG` / `INFO` / `WARNING` / … |

Configuration is loaded and validated at startup via `pydantic-settings`
(`config.Settings`) — environment variables take precedence over `.env`.

If `VOYAGE_API_KEY` is unset, RAG falls back to a local
`sentence-transformers` model (`all-MiniLM-L6-v2`, ~90MB, downloaded once).

## Requirements

- Python 3.11+
- Git available on `PATH` (the commit analyzer shells out to it)
