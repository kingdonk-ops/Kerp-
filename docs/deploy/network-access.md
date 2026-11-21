# Deploy: network access restrictions (IP allowlisting)

KERP has **no in-app IP allowlist** (consolidation diff §4.9 *Network restrictions*; enterprise
roadmap, 2026-11-21). Restricting where requests may come from is infrastructure configuration
of each deployment. Per-project or per-persona IP rules are a later item (TD-440).

## Where to restrict

| Layer | Use it for | Notes |
|---|---|---|
| **AWS WAF** on the load balancer or CloudFront distribution in front of the API and the web app | Source-IP allow or block lists for the whole deployment (for example only the company's egress ranges and VPN for `/api/v1/admin/*`) | Use WAF IP sets and rules scoped by path. Keep `/api/v1/auth/*`, `/invite`, `/reset-password` and the SES notification endpoint (diff §4.6) reachable for external users and AWS, or invites, password resets and bounce handling break. |
| **Entra ID Conditional Access** (named locations, compliant device) | Where company staff may complete Entra SSO (diff §4.5) | Applies to SSO sign-ins only. Password sign-in stays allowed by design (diff §4.5), so a WAF rule is needed if password sign-in must also be restricted. |
| **SCIM** (`/scim/v2/*`, diff §4.9) | Allow only Microsoft's provisioning service | Microsoft publishes the Entra provisioning IP ranges as a service tag; keep the WAF rule in step with it, and keep the bearer token as the real control. |
| **Security groups / private subnets** | Database, Redis, ClamAV, workers | Never reachable from the internet. |

## Client IP inside KERP

KERP uses the client IP for login lockout (T-0068), rate limits, audit and the signing
attestation (`modules/quality-and-inspection.md` §Signing attestation). It trusts
`X-Forwarded-For` only from the proxies listed in `FORWARDED_ALLOW_IPS` (uvicorn); set it to the
load balancer's addresses, never `*`. Otherwise every request appears to come from the proxy, and
a client could spoof its IP if the list were too wide.

## Field devices

Android tablets work offline and sync from site networks, mobile carriers and hotels. An IP
allowlist on the sync and auth endpoints would stop field work; restrict field access with the
device registry, revoke and wipe (diff §4.3) instead.
