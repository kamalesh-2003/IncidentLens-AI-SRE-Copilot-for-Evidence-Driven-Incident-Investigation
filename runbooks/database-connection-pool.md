---
title: Database Connection Pool Exhaustion
service: any
severity: high
symptoms: connection pool exhausted, too many connections, cannot acquire connection, database timeouts, pool saturation
---

## Database Connection Pool Exhaustion

The application cannot acquire database connections; requests block or fail while
waiting on an exhausted pool.

### Likely causes
- Connections leaked (not returned to the pool) by a recent code change.
- A traffic surge exceeding the configured pool size.
- Long-running or stuck queries holding connections open.

### Diagnosis
1. Check active vs. idle connection counts against the pool limit.
2. Look for a deploy that changed transaction scope or query patterns.
3. Identify long-running queries holding connections.

### Mitigation
- Roll back a change that leaks or long-holds connections.
- Increase the pool size or add a read replica to relieve pressure.
- Kill stuck queries and restart the service to reset the pool.
