"""Maker-checker execution (PRD §5 cross-cutting guardrail).

A privileged endpoint that needs dual control calls ``request_approval`` —
it get-or-creates a pending ``ApprovalRequest`` holding the action inputs
the checker signs off on. ``execute_approval`` dispatches on the recorded
action after ``decided_by != requested_by`` has been enforced by the view.

Executors keep the mutation logic next to the approval record so the
checker never re-enters payloads the maker typed.
"""
import logging
from decimal import Decimal

from django.conf import settings
from django.utils import timezone

from .audit import record_audit
from .models import ApprovalRequest

logger = logging.getLogger(__name__)


def payout_dual_threshold() -> Decimal:
    return Decimal(str(getattr(settings, 'PAYOUT_DUAL_APPROVAL_THRESHOLD',
                             '1000')))


def request_approval(*, request, action, entity, payload=None, reason=''):
    """Get-or-create the pending approval for (action, entity)."""
    ap, created = ApprovalRequest.objects.get_or_create(
        action=action,
        entity_type=entity.__class__.__name__,
        entity_id=str(entity.pk),
        status='pending',
        defaults={
            'payload': payload or {},
            'reason': reason,
            'requested_by': getattr(request, 'user', None),
        },
    )
    if created:
        record_audit(
            request=request, action='approval.requested', entity=ap,
            after={'for': action, 'entity_id': str(entity.pk),
                   'payload': payload or {}},
            reason=reason,
        )
    return ap


def _execute_payout_settled(approval, request):
    from apps.pay.models import Withdrawal

    withdrawal = Withdrawal.objects.get(pk=approval.entity_id)
    reference = (approval.payload.get('settlement_reference') or '').strip()
    if not reference:
        raise ValueError('Approved request is missing settlement_reference')
    if withdrawal.status == 'completed':
        return {'already_completed': True}
    before = {'status': withdrawal.status}
    withdrawal.status = 'completed'
    withdrawal.processed_at = timezone.now()
    withdrawal.flutterwave_transfer_id = reference
    withdrawal.save(update_fields=['status', 'processed_at',
                                   'flutterwave_transfer_id'])
    record_audit(
        request=request, action='finance.payout_settled', entity=withdrawal,
        before=before,
        after={'status': 'completed', 'settlement_reference': reference,
               'amount': str(withdrawal.amount)},
        reason=approval.payload.get('note') or 'approved manual settlement',
        approval=str(approval.pk),
    )
    return {'withdrawal': str(withdrawal.pk), 'status': 'completed'}


def _execute_capability_grant(approval, request):
    """GOV-01 — an approved sensitive-capability grant becomes active."""
    from .models import CapabilityGrant
    expires_raw = approval.payload.get('expires_at') or ''
    grant = CapabilityGrant.objects.create(
        user_id=approval.entity_id,
        capability=approval.payload['capability'],
        granted=True,
        granted_by=request.user,
        expires_at=expires_raw or None,
        reason=approval.reason,
    )
    record_audit(
        request=request, action='access.capability_grant', entity=grant,
        after={'user': str(grant.user_id),
               'capability': grant.capability},
        reason=approval.reason, approval=str(approval.pk),
    )
    return {'grant_id': grant.id, 'capability': grant.capability,
            'user': approval.entity_id}


_EXECUTORS = {
    'finance.payout_settled': _execute_payout_settled,
    'access.capability_grant': _execute_capability_grant,
}


def execute_approval(approval, request) -> dict:
    """Run the recorded action for an approved request; mark it executed."""
    executor = _EXECUTORS.get(approval.action)
    if executor is None:
        raise ValueError(f'No executor for action {approval.action}')
    result = executor(approval, request)
    approval.status = 'executed'
    approval.save(update_fields=['status'])
    return result
