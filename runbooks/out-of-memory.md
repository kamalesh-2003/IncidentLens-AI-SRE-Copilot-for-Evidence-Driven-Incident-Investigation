---
title: Out of Memory and Pod Restarts
service: any
severity: critical
symptoms: OOMKilled, memory leak, rising heap usage, pod restarts, container evicted, out of memory
---

## Out of Memory and Pod Restarts

A service's memory usage grows until the container is OOMKilled and restarted,
often in a repeating crash loop.

### Likely causes
- A memory leak introduced by a recent code change (unbounded cache, retained references).
- An unbounded in-memory buffer or queue growing under load.
- Memory limits set too low for the workload after a traffic increase.

### Diagnosis
1. Chart memory usage over time — a steady climb suggests a leak.
2. Correlate the onset with the most recent deploy.
3. Capture a heap dump or profile to find the dominant allocation.

### Mitigation
- Roll back the deploy suspected of introducing the leak.
- Restart affected pods to restore capacity while investigating.
- Raise the memory limit as a temporary stopgap if the workload legitimately grew.
