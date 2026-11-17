# Deploying outbound e-mail (AWS SES)

Prerequisites for the outbound e-mail service (`backend/app/core/mail/`, T-0058, spec diff
§4.6). The pilot runbook (T-0146) checks each item. Everything happens in the **deployment
region** (`KERP_AWS_REGION`, diff §4.4: `ap-southeast-2` for Australian deployments).

## 1. Sending domain: DKIM, SPF, DMARC

1. In SES (deployment region), create a **domain identity** for the sending domain, e.g.
   `mail.company.example` (a subdomain keeps the company's main-domain reputation separate).
2. Enable **Easy DKIM** (RSA 2048) and publish the three `CNAME` records SES shows in the
   domain's DNS. Wait until the identity shows *DKIM: successful* and *Verified*.
3. Set a **custom MAIL FROM domain** (e.g. `bounce.mail.company.example`) and publish its `MX`
   record and the SPF `TXT` record SES shows (`v=spf1 include:amazonses.com ~all`), so the
   envelope sender is SPF-aligned with the From domain.
4. Publish a **DMARC** record for the From domain, starting with monitoring, e.g.
   `_dmarc.mail.company.example TXT "v=DMARC1; p=none; rua=mailto:dmarc@company.example"`;
   move to `p=quarantine` / `p=reject` once the reports show DKIM and SPF pass and align.
5. Use an address on that domain as `KERP_MAIL_FROM_ADDRESS`, e.g.
   `KERP <no-reply@mail.company.example>`.

## 2. Production access (out of the sandbox)

A new SES account can only send to verified addresses ("sandbox"). Request **production
access** for the deployment region (SES console → *Account dashboard* → *Request production
access*): transactional mail (sign-in links, invites, password resets), expected volume, and
that bounces and complaints are handled automatically (step 3). Until it is granted, invite
and reset e-mails to unverified addresses fail (`failed`, `ses:MessageRejected` in the send
log).

## 3. Bounce and complaint wiring

1. Create an SNS **standard topic** in the deployment region, e.g. `kerp-mail-events`.
2. Create an SES **configuration set** (e.g. `kerp-mail`) with an **event destination** of
   type SNS → that topic, for the event types *Bounce* and *Complaint* (*Delivery* is optional
   and ignored). Set `KERP_MAIL_SES_CONFIGURATION_SET=kerp-mail`.
   (Alternatively: the identity's *Feedback notifications* for bounces and complaints → the
   topic. Either way SES publishes to the one topic.)
3. Set `KERP_MAIL_SNS_TOPIC_ARN` to the topic ARN and deploy. The API now accepts SNS
   messages of that topic only.
4. Add an **HTTPS subscription** to the topic with the endpoint
   `https://<deployment>/api/v1/mail/ses-events` (raw message delivery **off**). KERP confirms
   it automatically (it fetches the `SubscribeURL` only for the configured topic, from
   `https://sns.<region>.amazonaws.com/`). Check the subscription shows *Confirmed*.
5. The endpoint must be reachable from SNS (public HTTPS, valid certificate). It has no JWT;
   it verifies the SNS signature (SignatureVersion 2), the exact topic and the certificate
   host. Set the topic's *delivery policy* to retry for a few hours.
6. Test with the **SES mailbox simulator** from the deployment: send to
   `bounce@simulator.amazonses.com` and `complaint@simulator.amazonses.com` (e.g. a magic-link
   request for a synthetic test user with that address). Both addresses must appear on the
   suppression list (`GET /api/v1/mail/suppressions`, System admin) and the sends must show
   `bounced` / `complained` in `GET /api/v1/mail/sends`. Remove them afterwards
   (`DELETE /api/v1/mail/suppressions/{id}`).

## 4. Credentials

KERP never holds AWS keys in configuration or the repository. The worker (the only process
that calls SES) uses the AWS default credential chain: the ECS task role / EC2 instance role,
or credentials injected from the secret store. Minimal policy for that role:

```json
{
  "Effect": "Allow",
  "Action": "ses:SendEmail",
  "Resource": [
    "arn:aws:ses:<region>:<account>:identity/mail.company.example",
    "arn:aws:ses:<region>:<account>:configuration-set/kerp-mail"
  ]
}
```

The API process needs no AWS permission for mail (it fetches the SNS signing certificate over
public HTTPS).

## 5. Configuration variables

| Variable | Example | Notes |
|---|---|---|
| `KERP_MAIL_TRANSPORT` | `ses` | Required in production (start-up fails otherwise; the `log` transport is refused) |
| `KERP_MAIL_FROM_ADDRESS` | `KERP <no-reply@mail.company.example>` | A verified identity |
| `KERP_MAIL_SES_CONFIGURATION_SET` | `kerp-mail` | Event publishing to the topic |
| `KERP_MAIL_SNS_TOPIC_ARN` | `arn:aws:sns:ap-southeast-2:<account>:kerp-mail-events` | Must be in `KERP_AWS_REGION` |
| `KERP_AWS_REGION` | `ap-southeast-2` | Deployment region (SES client, SNS certificate host) |
| `KERP_MAIL_DEFAULT_LOCALE` | `en-AU` | Locale when the recipient has none |
| `KERP_WEB_ORIGIN` | `https://kerp.company.example` | Base of every link in an e-mail |
| `KERP_DEFAULT_TENANT_ID` | (deployment tenant) | Bounce/complaint rows are written under it |
| `KERP_REDIS_URL` | `redis://…` | The send queue (SAQ interactive queue) |

The API and the worker both refuse to start in production unless `KERP_MAIL_TRANSPORT=ses`,
`KERP_MAIL_FROM_ADDRESS`, `KERP_MAIL_SNS_TOPIC_ARN`, `KERP_WEB_ORIGIN` and
`KERP_DEFAULT_TENANT_ID` are set (the configuration set is optional but recommended).
