# Rollback Plan: CR-2026-0451 — Order Service Retry Logic

**Estimated rollback duration:** 15 minutes

## Steps

1. **Revert config to previous version**
   - Restore prior ConfigMap from version control:
     ```bash
     kubectl apply -f config/order-service-retry-v1.yaml -n orders
     ```
   - Verify ConfigMap revision matches pre-change snapshot (`notification.retry.max_attempts=3`).

2. **Restart pods**
   - Rolling restart to apply reverted configuration:
     ```bash
     kubectl rollout restart deployment/order-service -n orders
     kubectl rollout status deployment/order-service -n orders --timeout=10m
     ```

3. **Verify no retry loops**
   - Monitor `notification_retry_attempts_total` for 5 minutes — rate should return to pre-change baseline.
   - Confirm no messages stuck in retry state: `kubectl exec -n orders deploy/order-service -- curl -s localhost:9090/debug/retries | jq '.stuck_count'` returns `0`.

4. **Confirm notification queue depth normal**
   - Queue depth should stabilize below 500 within 10 minutes.
   - Alert on-call if depth exceeds 1,000 after rollback completes.
