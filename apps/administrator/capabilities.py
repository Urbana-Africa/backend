"""Named-capability authorization (PRD §5 permission model).

Broad role checks (``IsMarketer`` etc.) remain as the first gate; this layer
enforces the named capabilities the PRD demands for privileged actions —
e.g. ``marketing.send`` for campaign sends, ``finance.approve_payout`` for
payout approval. Executive read access does NOT imply mutation authority:
``c_level`` gets read capabilities but never send/approve/grant caps.

A denied capability check writes an ``AuditEvent`` (action
``permission.denied``) — that *is* the Phase-0 permission audit.
"""
import logging

from rest_framework.permissions import BasePermission, SAFE_METHODS

logger = logging.getLogger(__name__)

# role -> set of capabilities. ``*`` matches everything (superadmin only).
ROLE_CAPABILITIES = {
    'superadmin': {'*'},
    'marketer': {
        'marketing.view', 'marketing.review', 'marketing.send',
        'marketing.approve', 'marketing.configure', 'health.view', 'work.view',
    },
    'support_agent': {
        'customers.view', 'customers.manage', 'orders.view',
        'orders.edit_fulfillment', 'support.view', 'support.manage',
        'designers.view', 'designers.manage', 'catalog.view',
        'health.view', 'work.view', 'work.manage',
    },
    'product_manager': {
        'catalog.view', 'catalog.manage', 'catalog.publish',
        'orders.view', 'orders.edit_fulfillment', 'designers.view',
        'health.view', 'work.view', 'work.manage',
    },
    # Finance manager: money movement with maker-checker — they can be the
    # maker *or* the checker on a payout, never both on the same request.
    'finance': {
        'finance.view', 'finance.approve_payout', 'finance.mark_settled',
        'finance.refund', 'finance.reconcile', 'orders.view',
        'customers.view', 'audit.view', 'health.view', 'work.view',
        'work.manage',
    },
    # Executives read scorecards and domain reports — never money movement,
    # sends, role grants or publishing. Matches the PRD rights table.
    'c_level': {
        'dashboard.view', 'audit.view', 'health.view',
        'marketing.view', 'orders.view', 'catalog.view', 'customers.view',
        'designers.view', 'support.view',
        'finance.view', 'analytics.view', 'work.view',
    },
}

# Capabilities implied by Django staff/superuser status for non-role users.
_STAFF_CAPS = {'dashboard.view'}


def capabilities_for(user) -> set:
    """The effective capability set for a user, from their admin role."""
    if not user or not getattr(user, 'is_authenticated', False):
        return set()
    if getattr(user, 'is_superuser', False):
        return {'*'}
    caps = set(ROLE_CAPABILITIES.get(getattr(user, 'admin_role', None) or '', ()))
    if getattr(user, 'is_staff', False):
        caps |= _STAFF_CAPS
    return caps


def has_capability(user, capability: str) -> bool:
    caps = capabilities_for(user)
    return '*' in caps or capability in caps


class HasCapability(BasePermission):
    """DRF permission checking a named capability on the view or action.

    Views declare:
        required_capability = 'marketing.view'              # default for the view
        view_capability     = 'catalog.view'                # safe methods only
        manage_capability   = 'catalog.manage'              # mutating methods
        action_capabilities = {'send': 'marketing.send'}    # per-@action override
    A request is allowed when the user holds the resolved capability (or ``*``).
    """

    def _resolve_capability(self, request, view):
        action_caps = getattr(view, 'action_capabilities', {}) or {}
        action_name = getattr(view, 'action', None)
        if action_name and action_name in action_caps:
            return action_caps[action_name]
        if request.method in SAFE_METHODS:
            return (getattr(view, 'view_capability', None)
                    or getattr(view, 'required_capability', None))
        return (getattr(view, 'manage_capability', None)
                or getattr(view, 'required_capability', None))

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return False

        capability = self._resolve_capability(request, view)
        if not capability:
            # No capability declared — the view relies on other permission
            # classes; this layer does not grant on its own.
            return True

        if has_capability(user, capability):
            return True

        # Permission audit: record the denial (PRD §5 — no silent blocks).
        from .audit import record_audit
        record_audit(
            request=request,
            action='permission.denied',
            entity_type='capability',
            entity_id=capability,
            after={'path': request.path, 'method': request.method,
                   'action': getattr(view, 'action', None) or ''},
            reason=f'missing capability {capability}',
        )
        return False
