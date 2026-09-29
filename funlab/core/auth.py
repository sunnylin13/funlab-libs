
from functools import wraps

from flask import current_app, render_template
from flask_login import current_user
from jinja2 import TemplateNotFound

from funlab.core.policy import has_role


def _authorization_enabled() -> bool:
    """Return whether the current app has a real auth provider enabled."""
    try:
        return bool(getattr(current_app, 'authorization_enabled', False))
    except RuntimeError:
        return False


def _forbidden_response():
    """Return a consistent forbidden response.

    ``error-403.html`` ships with the funlab-flaskr base app; standalone or
    embedded apps without that template fall back to a plain-text 403 instead
    of raising TemplateNotFound (which would surface as a 500). Errors raised
    *inside* the template are not swallowed — only the missing-template case.
    """
    try:
        return render_template('error-403.html'), 403
    except TemplateNotFound:
        return 'Forbidden', 403


def _handle_unauthenticated():
    """Dispatch unauthenticated access according to the app security mode."""
    if not _authorization_enabled():
        return _forbidden_response()

    login_manager = getattr(current_app, 'login_manager', None)
    if login_manager is not None and hasattr(login_manager, 'unauthorized'):
        return login_manager.unauthorized()

    return _forbidden_response()


def _require_authenticated():
    """Return an auth failure response when the current user is anonymous."""
    if not getattr(current_user, 'is_authenticated', False):
        return _handle_unauthenticated()
    return None


def evaluate_policy(policy):
    """Evaluate a policy against current_user and return failure response or None."""
    auth_failure = _require_authenticated()
    if auth_failure is not None:
        return auth_failure

    if not policy(current_user):
        return _forbidden_response()

    return None


def policy_required(policy):
    """Protect a route with a reusable authorization policy."""

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            failure = evaluate_policy(policy)
            if failure is not None:
                return failure

            return func(*args, **kwargs)

        return wrapper

    return decorator


def role_required(roles: list | tuple):
    """Protect a route by comparing ``current_user.role`` against ``roles``.

    Legacy API: new code should compose policies from ``funlab.core.policy``
    and use ``policy_required``. Matching is case-insensitive — the same rule
    as :func:`funlab.core.policy.has_role`, which this delegates to so there
    is exactly one role-comparison implementation (ADR-045).
    """

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            auth_failure = _require_authenticated()
            if auth_failure is not None:
                return auth_failure

            if not has_role(current_user, *roles):
                return _forbidden_response()

            return func(*args, **kwargs)

        return wrapper

    return decorator


def admin_required(func):
    from funlab.core.policy import is_admin

    return policy_required(is_admin)(func)
