"""The ``onboarding`` module owns no table (T-0023): it writes the tenant through core
provisioning (``app.core.tenancy.provision``), the settings record through
``settings.service``, the account and its grants through ``admin.service`` and the demo
project through ``projects.service``."""
