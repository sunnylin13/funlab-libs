from __future__ import annotations


"""Shared authorization policies.

Keep only the minimum reusable primitives and compose them at call sites.
"""


def is_admin(user) -> bool:
    """Return whether the user has administrator privileges."""
    return bool(user and getattr(user, 'is_admin', False))


def has_role(user, *roles: str) -> bool:
    """Return whether the user role matches one of the expected roles."""
    if not user:
        return False
    user_role = str(getattr(user, 'role', '') or '').lower()
    expected = {str(role).lower() for role in roles}
    return user_role in expected


def is_supervisor(user) -> bool:
    """Return whether the user is a supervisor."""
    return has_role(user, 'supervisor')


def is_authenticated_user(user) -> bool:
    """Return whether the user is authenticated."""
    return bool(user and getattr(user, 'is_authenticated', False))
