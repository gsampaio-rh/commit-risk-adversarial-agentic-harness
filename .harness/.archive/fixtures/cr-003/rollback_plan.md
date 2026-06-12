# Rollback Plan: CR-2026-0453 — Auth Gateway TLS Rotation and CORS Update

**Estimated rollback duration:** 20 minutes

## Steps

1. **Restore previous certificates from vault**
   - Retrieve pre-rotation cert chain from vault path `secret/platform/auth-gateway/tls/pre-20260607`:
     ```bash
     vault kv get -field=fullchain secret/platform/auth-gateway/tls/pre-20260607 > /tmp/prev.fullchain.pem
     vault kv get -field=key secret/platform/auth-gateway/tls/pre-20260607 > /tmp/prev.key
     ```
   - Apply previous secret and patch ingress:
     ```bash
     kubectl create secret tls auth-gateway-tls-rollback \
       --cert=/tmp/prev.fullchain.pem --key=/tmp/prev.key \
       -n platform --dry-run=client -o yaml | kubectl apply -f -
     kubectl patch ingress auth-gateway -n platform \
       -p '{"spec":{"tls":[{"secretName":"auth-gateway-tls-rollback"}]}}'
     ```

2. **Revert CORS config**
   - Restore previous allowed origins list:
     ```bash
     kubectl patch configmap auth-gateway-cors -n platform \
       --type merge -p '{"data":{"allowed_origins":"https://app.internal"}}'
     ```

3. **Restart auth-gateway**
   - Rolling restart to apply reverted configuration:
     ```bash
     kubectl rollout restart deployment/auth-gateway -n platform
     kubectl rollout status deployment/auth-gateway -n platform --timeout=10m
     ```

4. **Verify rollback success**
   - Confirm TLS handshake uses previous certificate (check serial number matches pre-change export).
   - Verify partner domain CORS preflight returns expected failure (partner not in allowed list).
   - Confirm `order-service` token acquisition via auth-gateway succeeds.
