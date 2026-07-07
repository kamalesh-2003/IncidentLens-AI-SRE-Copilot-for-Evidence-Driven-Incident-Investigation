---
title: High HTTP Error Rate
service: any
severity: critical
symptoms: elevated 5xx errors, failed requests, error rate spike after a deploy, increased exception count
---

## High HTTP Error Rate

A sudden rise in 5xx responses or application exceptions, most often immediately
following a deploy or configuration change.

### Likely causes
- A recent deploy introduced a regression or a broken migration.
- A downstream dependency (database, cache, third-party API) is failing.
- A bad feature flag or configuration value was rolled out.

### Diagnosis
1. Compare the error-rate spike start time against the most recent deploy.
2. Inspect logs for the dominant exception type and stack trace.
3. Check the health of downstream dependencies for the affected service.

### Mitigation
- If a deploy correlates with the spike, roll back to the previous release.
- Disable the offending feature flag if one was recently changed.
- Fail over or shed load from an unhealthy dependency.
