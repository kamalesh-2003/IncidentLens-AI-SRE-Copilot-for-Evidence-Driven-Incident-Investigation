---
title: Elevated CPU and Throttling
service: any
severity: high
symptoms: CPU saturation, high CPU usage, CPU throttling, hot loop, autoscaling maxed out, compute bound
---

## Elevated CPU and Throttling

CPU utilization is pinned near its limit, triggering throttling and degraded
performance even when memory and error rates look healthy.

### Likely causes
- An inefficient algorithm or hot loop introduced by a recent change.
- Expensive serialization, compression, or crypto work in the request path.
- A traffic increase pushing the service past its provisioned capacity.

### Diagnosis
1. Capture a CPU profile to find the dominant function.
2. Correlate the onset with the most recent deploy.
3. Check whether autoscaling is already at its maximum replica count.

### Mitigation
- Roll back the deploy that introduced the expensive code path.
- Scale out horizontally or raise the CPU limit to relieve throttling.
- Move heavy work off the request path (async job, cache, precompute).
