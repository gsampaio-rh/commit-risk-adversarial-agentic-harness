# Runbook: CR-2026-0451 — Order Service Retry Logic

**Change:** Add exponential backoff retry for failed notification queue messages  
**Affected services:** `order-service`, `notification-queue`  
**Estimated duration:** 90 minutes

## Steps

1. **Pre-flight health check**
   - Confirm `order-service` and `notification-queue` report healthy in the observability dashboard.
   - Verify notification queue depth is below 500 messages: `kubectl exec -n orders deploy/order-service -- curl -s localhost:9090/metrics | grep notification_queue_depth`.
   - Record baseline retry-failure rate from Grafana panel `order-service/notification-retries`.

2. **Deploy new config**
   - Apply updated retry configuration to the `order-service` ConfigMap:
     ```bash
     kubectl apply -f config/order-service-retry-v2.yaml -n orders
     ```
   - Config keys: `notification.retry.max_attempts=5`, `notification.retry.backoff_ms=1000,2000,4000,8000,16000`.

3. **Restart order-service pods**
   - Perform rolling restart to pick up config changes:
     ```bash
     kubectl rollout restart deployment/order-service -n orders
     kubectl rollout status deployment/order-service -n orders --timeout=10m
     ```
   - Confirm all pods reach `Ready` state before proceeding.

4. **Verify retry metrics**
   - Wait 5 minutes for metrics to populate.
   - Check Prometheus: `rate(notification_retry_attempts_total{service="order-service"}[5m])` shows non-zero on simulated failures.
   - Confirm `notification-queue` consumer lag remains stable.

5. **Smoke test notification delivery**
   - Trigger test order via staging endpoint: `POST /internal/orders/test-notification`.
   - Verify message appears in `notification-queue` and is consumed within SLA.
   - Confirm no duplicate deliveries in downstream audit log.

6. **Rollback trigger criteria**
   - Abort and execute rollback if any of the following occur:
     - Notification queue depth exceeds 2,000 for more than 10 minutes.
     - Retry loop detected (same message ID retried > 10 times).
     - Order processing error rate increases by > 5% compared to pre-change baseline.
