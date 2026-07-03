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

You should see the alert logged and receive `{"received": true, ...}`.

## Architecture

```
webhook  →  orchestrator (LangGraph)
              ├─ commit_analyzer   (git log/diff + Claude tool use)
              ├─ runbook_retriever (embeddings over runbooks/)
              ├─ impact_estimator  (metrics store)
              ├─ slack_poster      (Block Kit → Slack webhook)
              └─ postmortem_gen    (on resolve → Markdown)
```

## Environment

Copy `.env.example` to `.env` and fill in:

| Variable            | Required | Purpose                                              | Where to get it |
|---------------------|----------|------------------------------------------------------|-----------------|
| `ANTHROPIC_API_KEY` | yes      | Claude reasoning across all stages                   | https://console.anthropic.com/settings/keys |
| `SLACK_WEBHOOK_URL` | no       | Send briefs to Slack (else prints Block Kit JSON)    | https://api.slack.com/messaging/webhooks |
| `DEMO_GIT_REPO_PATH`| no       | Repo the commit analyzer inspects                    | defaults to `./demo/app` |
| `VOYAGE_API_KEY`    | no       | High-quality embeddings for runbook RAG              | https://www.voyageai.com/ |
| `PROMETHEUS_URL`    | no       | Real metrics source (else uses a mock store)         | your Prometheus server |

If `VOYAGE_API_KEY` is unset, RAG falls back to a local
`sentence-transformers` model (`all-MiniLM-L6-v2`, ~90MB, downloaded once).

## Requirements

- Python 3.11+
- Git available on `PATH` (the commit analyzer shells out to it)
