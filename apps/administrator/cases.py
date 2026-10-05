"""Support case lifecycle (PRD SUP-02).

Canonical flow: new(open) → triaged → investigating → awaiting_party →
decision → action_pending → resolved, with resolved/closed → reopened.

Legacy statuses (``in_progress``, ``waiting``) stay valid so historical
tickets and the reply-driven flow keep working; they participate in the
map as aliases of ``investigating`` / ``awaiting_party``.

Every transition must record actor + reason (and optional evidence) in the
audit trail — enforced by ``apply_ticket_transition``, used by both the
``/manage/tickets`` action and the reply-with-status endpoint.
"""


# Legal forward edges per state. Terminal states only reopen.
TICKET_TRANSITIONS = {
    'open':           {'triaged', 'in_progress', 'investigating', 'resolved', 'closed'},
    'triaged':        {'investigating', 'in_progress', 'awaiting_party', 'waiting', 'resolved'},
    'in_progress':    {'triaged', 'awaiting_party', 'waiting', 'decision', 'resolved', 'closed'},
    'investigating':  {'awaiting_party', 'waiting', 'decision', 'resolved'},
    'awaiting_party': {'investigating', 'in_progress', 'decision', 'resolved'},
    'waiting':        {'investigating', 'in_progress', 'decision', 'resolved'},
    'decision':       {'action_pending', 'resolved'},
    'action_pending': {'resolved', 'closed'},
    'resolved':       {'reopened'},
    'closed':         {'reopened'},
    'reopened':       {'triaged', 'investigating', 'in_progress'},
}


def allowed_transitions(ticket):
    return sorted(TICKET_TRANSITIONS.get(ticket.status, set()))


def apply_ticket_transition(ticket, new_status, *, reason, evidence=''):
    """Validate + apply a SUP-02 status change.

    Returns ``None`` on success or an error dict for a 400 response.
    The caller persists the audit event (it owns the request context).
    """
    if new_status == ticket.status:
        return None  # no-op
    err = validate_transition(
        ticket, 'status', new_status, TICKET_TRANSITIONS,
        reason=reason, reason_label='case status change (SUP-02)',
    )
    if err:
        return err
    ticket.status = new_status
    ticket.save(update_fields=['status', 'updated_at'])
    return None


# ---------------------------------------------------------------------------
# OPS-03 — order / order-item fulfillment transitions.
# Delivered may only be reached via shipped; terminal states reopen through
# the returns flow (a new ReturnRequest), never by flipping status back.
# ---------------------------------------------------------------------------

ORDER_TRANSITIONS = {
    'pending':    {'processing', 'cancelled'},
    'processing': {'shipped', 'cancelled'},
    'shipped':    {'delivered', 'cancelled'},
    'delivered':  {'returned'},
    'returned':   set(),
    'cancelled':  set(),
}

ORDER_ITEM_TRANSITIONS = dict(ORDER_TRANSITIONS)

CUSTOMER_STATUS_TRANSITIONS = {
    'pending':  {'received', 'returned'},
    'received': {'returned'},
    'returned': set(),
}


def validate_transition(obj, field, new_value, transitions, *,
                        reason, reason_label='status change'):
    """Shared OPS-03/SUP-02 gate: returns an error dict or ``None``.

    Same-status writes are idempotent no-ops (duplicate carrier/webhook
    events can never manufacture a state). A real move requires a reason.
    """
    if new_value not in _all_states(transitions):
        return {'status': 'error',
                'message': f"Invalid status '{new_value}'. "
                           f"Choose from {sorted(_all_states(transitions))}."}
    if new_value == getattr(obj, field):
        return None  # idempotent no-op
    if not reason or not reason.strip():
        return {'status': 'error',
                'message': f'A reason is required for every {reason_label}.'}
    allowed = transitions.get(getattr(obj, field), set())
    if new_value not in allowed:
        return {'status': 'error',
                'message': f"Cannot move {getattr(obj, field)} → {new_value}.",
                'allowed': sorted(allowed)}
    return None


def _all_states(transitions):
    states = set(transitions) | {s for targets in transitions.values()
                                 for s in targets}
    return states
