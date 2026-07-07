---
title: High Request Latency
service: any
severity: high
symptoms: p99 latency spike, slow responses, request timeouts, degraded throughput, slow database queries
---

## High Request Latency

Response times (p95/p99) climb well above baseline; requests may time out even
though error rates remain normal.

### Likely causes
- A newly introduced N+1 query or an unindexed query in the hot path.
- Connection pool or thread pool contention under load.
- A slow downstream dependency adding tail latency.
- Garbage-collection pauses or CPU saturation.

### Diagnosis
1. Break latency down by endpoint to isolate the slow path.
2. Look for a deploy that added database calls or removed a cache.
3. Inspect slow-query logs and downstream call durations.

### Mitigation
- Roll back a deploy that introduced expensive queries.
- Add or restore caching for the hot path.
- Scale out the service or increase pool sizes to relieve contention.
