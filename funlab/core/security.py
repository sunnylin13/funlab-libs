from __future__ import annotations

from enum import Enum


class SecurityMode(str, Enum):
    """Application security posture.

    ``PUBLIC`` means no authentication provider is installed and the app should
    keep public-facing routes operational without requiring login.

    ``SECURED`` means an auth provider has been wired into Flask-Login and
    authenticated/authorized routes may enforce identity checks.
    """

    PUBLIC = "public"
    SECURED = "secured"