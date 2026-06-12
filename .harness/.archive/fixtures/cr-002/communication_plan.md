# Communication Plan: CR-2026-0452

**Audience:** External customers and integration partners  
**Customer impact:** New optional API parameter — existing integrations unaffected

## Notification

Customer notification required due to customer-facing API change on `/v2/payments`.

**Message template:**

> Payment API v2 now supports multi-currency. Existing integrations unaffected. Documentation at developer portal.

**Channels:**
- Developer portal changelog — publish 48 hours before change window
- Customer email to registered API integration contacts
- Status page — scheduled maintenance notice for payment-api (03:00–05:30 UTC)

**Documentation:**
- Update API reference with optional `currency` parameter (ISO 4217)
- Add migration guide for partners opting into multi-currency
