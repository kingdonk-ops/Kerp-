# notifications — in-app notifications behind a channel interface (T-0019)

A notification is one row per recipient in one project. The row is the recipient's in-app
inbox entry. Delivery goes through named channels: `in_app` is the only one built here. FCM
push (T-0144) and e-mail (TD-084) plug in as further channels without changing any caller
(TD-168 decision 5a).

| File | What |
|---|---|
| `models.py` | `oe_notifications_notification` (`BaseRecordMixin`, project RLS, migration `0010`) |
| `channels.py` | `NotificationChannel` protocol, `Recipient`, `DeliveryResult`, `InAppChannel`, the channel registry (`register_channel`, `get_channel`) |
| `schemas.py` | `NotificationCreate` (service, may pin), `NotificationPost` (API, no pin), `NotificationRead`, `NotificationSent` |
| `service.py` | `notify`, `list_notifications`, `get_notification`, `mark_read`, `clear_pins` |
| `router.py` | `/api/v1/notifications/projects/{project_id}/notifications...` |

## Record

| Column | Rule |
|---|---|
| `recipient_id` | a user with a membership in the project (else `RecipientNotMemberError`, API 422) |
| `kind`, `params` | message key (`handover.received`) and its values (≤ 20 scalar values). No text is stored: the client renders `t("notifications.kind.<kind>", params)` |
| `label` | optional label key (`handover`), rendered with `t()` too |
| `priority` | `low`, `normal` (default), `high`, `critical` |
| `pinned`, `pin_key` | a pin needs the key of the action that clears it, e.g. `handover.ack:<id>`; `unpinned_at` is set when it is cleared |
| `read_at` | set by the recipient once; reading does not unpin |
| `subject_type`, `subject_id` | optional reference to the record the notification is about (no FK) |

The list is the caller's own notifications in the project: **pinned first, then priority
(critical first), then newest** (E-04-11 handover, `docs/spec/modules/handover.md` §6).

## Channels

```python
class NotificationChannel(Protocol):
    name: str

    def deliver(self, notification: Notification, recipient: Recipient) -> DeliveryResult: ...
```

`KERP_NOTIFICATION_CHANNELS` (JSON list, default `["in_app"]`; must contain `in_app`) names the
channels every notification goes to. A channel module registers itself with
`register_channel("fcm", FcmChannel)`. `notify` stores the row, then runs `in_app` first and the
others once each in the configured order. A channel that raises, returns `ok=False` or is not
registered gives a failed `DeliveryResult` (detail: a code or the exception type, never its
text) and a log line; the in-app row stays and the remaining channels still run. All channels
run synchronously inside the caller's transaction for now; remote channels move to an
after-commit job with a delivery log and retries (TD-238).

## Using it from another module

```python
from app.modules.notifications import service as notifications
from app.modules.notifications.schemas import NotificationCreate

notifications.notify(
    session,
    NotificationCreate(
        recipient_id=user_id,
        kind="handover.received",
        priority="high",
        label="handover",
        pinned=True,
        pin_key=f"handover.ack:{handover.id}",
        subject_type="handover.handover",
        subject_id=handover.id,
    ),
    actor_id=actor_id,
)
# later, when the recipient acknowledges:
notifications.clear_pins(session, pin_key=f"handover.ack:{handover.id}", recipient_id=user_id)
```

The session must be bound to the project.

## API

The project is the one in the path; a non-member or a deleted project is 404.

| Endpoint | Who |
|---|---|
| `POST .../notifications` | `admin` (Project Administrator, PER-09); body `NotificationPost` (no pin: an API caller has no action to clear it); answers the row and each channel's result |
| `GET .../notifications?unread_only=&limit=&offset=` | any member; their own notifications only (`limit` 1–200, default 50) |
| `POST .../notifications/{id}/read` | the recipient; anybody else gets 404 |

## Tests

`tests/test_notifications.py` (SQLite and PostgreSQL): contract, validation, channel registry,
fan-out to a fake second channel, a failing channel, order, pins, tenancy, API.
