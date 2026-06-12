# Runbook: CR-2026-0452 — Payment API Multi-Currency Support

**Change:** Add currency column and multi-currency API support  
**Affected services:** `payment-api`, `payment-db`  
**Estimated duration:** 120 minutes

## Steps

1. **Enable maintenance mode on payment-api**
   - Set maintenance flag to drain in-flight requests:
     ```bash
     kubectl set env deployment/payment-api MAINTENANCE_MODE=true -n payments
     kubectl rollout status deployment/payment-api -n payments --timeout=5m
     ```
   - Confirm `/health` returns `503` and no new payment requests are accepted.

2. **Backup payment-db**
   - Take logical backup of the active payment database:
     ```bash
     pg_dump -h payment-db.internal -U payment_admin payment_production \
       -f /backups/payment-db-pre-migration-20260607.sql
     ```
   - Verify backup file size and checksum before proceeding.

3. **Run migration script against payment-db-v3**
   - Connect to `payment-db-v3` and run schema migration:
     ```bash
     psql -h payment-db-v3.internal -U payment_admin -d payment_production
     ```
   - Execute migration:
     ```sql
     ALTER TABLE payment_transactions ADD COLUMN currency VARCHAR(3);
     ```
   - **Note:** Runbook references legacy host `payment-db-v3` per original deployment documentation.

4. **Deploy updated payment-api**
   - Deploy version `2.9.0` with multi-currency support:
     ```bash
     kubectl set image deployment/payment-api \
       payment-api=registry.internal/payment-api:2.9.0 -n payments
     kubectl rollout status deployment/payment-api -n payments --timeout=15m
     ```

5. **Run verification queries**
   - Confirm column exists and default values applied:
     ```sql
     SELECT column_name, data_type FROM information_schema.columns
     WHERE table_name = 'payment_transactions' AND column_name = 'currency';
     ```
   - Run smoke test transaction with `currency=EUR`.

6. **Disable maintenance mode**
   - Remove maintenance flag:
     ```bash
     kubectl set env deployment/payment-api MAINTENANCE_MODE- -n payments
     ```
   - Confirm payment processing resumes with normal throughput.

7. **Verify /v2/payments endpoint**
   - Test optional currency parameter:
     ```bash
     curl -X POST https://api.internal/v2/payments \
       -H "Authorization: Bearer $TEST_TOKEN" \
       -d '{"amount": 100, "currency": "GBP"}'
     ```
   - Verify response includes `currency` field and HTTP 201 status.
