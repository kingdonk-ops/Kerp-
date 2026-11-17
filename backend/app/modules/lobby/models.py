"""The ``lobby`` module owns no table (T-0040): it reads projects through the narrow lobby read
of ``projects.service`` and the caller's memberships through core auth. Access requests
(T-0050) live in the ``access_requests`` module."""
