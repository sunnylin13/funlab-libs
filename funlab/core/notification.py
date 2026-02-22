"""Abstract notification provider interface for funlab.

``INotificationProvider`` defines the contract that every notification back-end
must satisfy.  Two concrete implementations exist:

* ``PollingNotificationProvider`` (funlab-flaskr, built-in)
  – in-memory storage, browser polls ``/notifications/poll`` periodically.
  – ``supports_realtime = False``

* ``SSEService`` (funlab-sse plugin)
  – DB-backed persistence, real-time Server-Sent Events push.
  – ``supports_realtime = True``

Registration on the app::

    app.set_notification_provider(provider)

The active provider is always accessible as::

    app.notification_provider          # instance
    current_app.notification_provider  # inside a request context
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from flask import Blueprint


class INotificationProvider(ABC):
    """Common interface for all event / notification delivery providers.

    Implementations must override all ``@abstractmethod`` members.  Optional
    extended capabilities (real-time events, connection tracking) have
    no-op defaults so that the polling provider compiles without change.
    """

    # ------------------------------------------------------------------
    # Core notification methods  (all providers must implement)
    # ------------------------------------------------------------------

    @abstractmethod
    def send_user_notification(
        self,
        title: str,
        message: str,
        target_userid: int = None,
        priority: str = 'NORMAL',
        expire_after: int = None,
    ) -> None:
        """Send a notification to *target_userid*.

        When *target_userid* is ``None`` the notification is treated as global
        (same behaviour as :meth:`send_global_notification`).
        """
        ...

    @abstractmethod
    def send_global_notification(
        self,
        title: str,
        message: str,
        priority: str = 'NORMAL',
        expire_after: int = None,
    ) -> None:
        """Broadcast a notification to all users."""
        ...

    @abstractmethod
    def fetch_unread(self, user_id: int) -> list[dict]:
        """Return all undismissed / unread notifications for *user_id*.

        Each dict must contain at minimum::

            {
                "id":         int,
                "event_type": str,
                "priority":   str,   # 'LOW' | 'NORMAL' | 'HIGH' | 'CRITICAL'
                "created_at": str,   # ISO-8601
                "payload":    dict,  # {"title": ..., "message": ...}
                "is_recovered": bool,
            }
        """
        ...

    @abstractmethod
    def dismiss_items(self, user_id: int, item_ids: list[int]) -> None:
        """Dismiss specific notifications by *item_ids* for *user_id*."""
        ...

    @abstractmethod
    def dismiss_all(self, user_id: int) -> None:
        """Dismiss every notification for *user_id*."""
        ...

    # ------------------------------------------------------------------
    # Extended capabilities  (no-op defaults; SSE provider overrides)
    # ------------------------------------------------------------------

    def send_event(
        self,
        event_type: str,
        target_userid: int,
        payload: dict,
        priority: str = 'NORMAL',
        expire_after: int = None,
    ) -> bool:
        """Send an arbitrary typed event to a connected user.

        Returns ``True`` if the event was delivered, ``False`` when the
        provider does not support real-time push or the user is offline.
        Polling provider default is a no-op returning ``False``.
        """
        return False

    def get_connected_users(self, event_type: str) -> set:
        """Return the set of ``user_id`` values currently subscribed to *event_type*.

        Polling provider has no connection concept; returns an empty set.
        """
        return set()

    @property
    def supports_realtime(self) -> bool:
        """``True`` when the provider delivers events via a persistent push channel (SSE)."""
        return False

    # ------------------------------------------------------------------
    # Optional route registration (for SSE-specific endpoints like /sse/*)
    # ------------------------------------------------------------------

    def register_routes(self, blueprint: Blueprint) -> None:
        """Register provider-specific HTTP routes on *blueprint*.

        This is for routes that are unique to the provider itself—e.g. SSEService
        uses this to register ``/sse/<event_type>``, ``/ssetest``, etc.

        The generic ``/notifications/*`` routes (poll, clear, dismiss) are
        registered directly by ``FunlabFlask`` and dispatch through
        ``current_app.notification_provider`` at request time, so they work
        transparently regardless of which provider is active.

        The default implementation is a no-op; override in providers that need
        provider-specific endpoints.
        """
        pass
