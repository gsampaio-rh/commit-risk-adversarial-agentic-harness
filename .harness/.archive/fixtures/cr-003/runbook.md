# Runbook: CR-2026-0453 — Auth Gateway TLS Rotation and CORS Update

**Change:** Rotate TLS certificates and add partner domain to CORS policy  
**Affected services:** `auth-gateway`, `order-service` (dependency verification)  
**Estimated duration:** 90 minutes

## Steps

1. **Verify current cert expiry**
   - Check certificate expiration on `auth-gateway`:
     ```bash
     echo | openssl s_client -connect auth-gateway.internal:443 2>/dev/null \
       | openssl x509 -noout -dates
     ```
   - Confirm current cert expires within 14 days (justifies rotation window).
   - Export current cert chain to vault backup path before replacement.

2. **Deploy new certificates to auth-gateway**
   - Install renewed certificates from internal PKI:
     ```bash
     kubectl create secret tls auth-gateway-tls-v202606 \
       --cert=/certs/auth-gateway.fullchain.pem \
       --key=/certs/auth-gateway.key \
       -n platform --dry-run=client -o yaml | kubectl apply -f -
     kubectl patch ingress auth-gateway -n platform \
       -p '{"spec":{"tls":[{"secretName":"auth-gateway-tls-v202606"}]}}'
     ```

3. **Update CORS config**
   - Add partner domain to allowed origins:
     ```bash
     kubectl patch configmap auth-gateway-cors -n platform \
       --type merge -p '{"data":{"allowed_origins":"https://app.internal,https://partner.example.com"}}'
     ```
   - Rolling restart to apply CORS changes:
     ```bash
     kubectl rollout restart deployment/auth-gateway -n platform
     kubectl rollout status deployment/auth-gateway -n platform --timeout=10m
     ```

4. **Verify TLS handshake**
   - Validate new certificate chain:
     ```bash
     curl -vI https://auth-gateway.internal/health 2>&1 | grep "SSL certificate verify ok"
     ```
   - Confirm intermediate CA is present and expiry date matches new cert.

5. **Smoke test partner domain authentication**
   - Test OAuth flow from partner origin:
     ```bash
     curl -X POST https://auth-gateway.internal/oauth/token \
       -H "Origin: https://partner.example.com" \
       -d "grant_type=client_credentials&client_id=$PARTNER_TEST_ID"
     ```
   - Verify `order-service` can still obtain tokens via auth-gateway dependency.
   - Confirm CORS preflight succeeds for partner domain.
