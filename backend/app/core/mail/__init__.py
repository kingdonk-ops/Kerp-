"""Outbound e-mail core service (T-0058, diff §4.6). See ``README.md``.

Entry point: ``app.core.mail.service.send_mail(session, template_key, to, context, locale)``.
Kept import-free: ``app.core.db`` imports ``models`` while it is still initialising.
"""
