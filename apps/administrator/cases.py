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
    if not reason or not reason.strip():
        return {
            'status': 'error',
            'message': 'A reason is required for every case status change '
                       '(SUP-02).',
        }
    allowed = TICKET_TRANSITIONS.get(ticket.status, set())
    if new_status not in allowed:
        return {
            'status': 'error',
            'message': f"Cannot move {ticket.status} → {new_status}.",
            'allowed': sorted(allowed),
        }
    ticket.status = new_status
    ticket.save(update_fields=['status', 'updated_at'])
    return None
