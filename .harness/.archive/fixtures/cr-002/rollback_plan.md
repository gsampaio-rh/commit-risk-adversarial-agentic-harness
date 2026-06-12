# Rollback Plan: CR-2026-0452 — Payment API Multi-Currency Support

**Estimated rollback duration:** 30 minutes

## Steps

1. **Enable maintenance mode**
   - Drain payment-api traffic:
     ```bash
     kubectl set env deployment/payment-api MAINTENANCE_MODE=true -n payments
     ```

2. **Restore database from pre-migration backup**
   - Restore database from pre-migration backup taken in step 2:
     ```bash
     psql -h payment-db.internal -U payment_admin -d payment_production \
       -f /backups/payment-db-pre-migration-20260607.sql
     ```
   - Drop and recreate database if restore fails on first attempt.

3. **Redeploy previous payment-api version**
   - Roll back to version `2.8.1`:
     ```bash
     kubectl set image deployment/payment-api \
       payment-api=registry.internal/payment-api:2.8.1 -n payments
     kubectl rollout status deployment/payment-api -n payments --timeout=15m
     ```

4. **Disable maintenance mode and verify**
   - Remove maintenance flag and confirm `/v2/payments` accepts requests without currency parameter.
   - Run end-to-end payment test to verify processing restored.
