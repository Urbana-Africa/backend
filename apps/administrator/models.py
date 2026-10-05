"""Control-center governance entities (PRD Phase 0 — truth & safety).

`AuditEvent` is the immutable record of every privileged staff action.
`DataQualityCheck`, `ReconciliationRun` and `ReconciliationException` are the
daily data-health instrumentation: they compare canonical sources (payments,
orders, analytics events) and surface drift instead of letting dashboards
show fabricated or silently-stale numbers.
"""
from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.utils.uuid_generator import generate_custom_id


class AuditEvent(models.Model):
    """Append-only audit record for privileged actions.

    Required by PRD §5: actor, effective role, action, entity, before/after,
    reason, request id, IP/device metadata and timestamp. Rows must never be
    updated or deleted — corrections are recorded as new events. No secrets
    or full payment details may be stored in ``before``/``after``.
    """

    id = models.CharField(
        primary_key=True, max_length=50, default=generate_custom_id, editable=False
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='audit_events',
    )
    actor_email = models.CharField(max_length=255, blank=True, default='')
    actor_role = models.CharField(
        max_length=30, blank=True, default='',
        help_text="Effective admin_role at the time of the action.",
    )
    action = models.CharField(max_length=100, db_index=True)
    entity_type = models.CharField(max_length=100, db_index=True, blank=True, default='')
    entity_id = models.CharField(max_length=100, db_index=True, blank=True, default='')
    before = models.JSONField(default=dict, blank=True)
    after = models.JSONField(default=dict, blank=True)
    reason = models.TextField(blank=True, default='')
    approval = models.CharField(
        max_length=255, blank=True, default='',
        help_text="Approver identity / approval reference for maker-checker flows.",
    )
    request_id = models.CharField(max_length=100, blank=True, default='')
    ip_address = models.CharField(max_length=64, blank=True, default='')
    user_agent = models.CharField(max_length=300, blank=True, default='')
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['entity_type', 'entity_id', '-created_at']),
            models.Index(fields=['action', '-created_at']),
        ]

    def save(self, *args, **kwargs):
        if self.pk and AuditEvent.objects.filter(pk=self.pk).exists():
            raise ValueError("AuditEvent rows are append-only and cannot be updated")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("AuditEvent rows are append-only and cannot be deleted")

    def __str__(self):
        who = self.actor_email or 'system'
        return f"{who}: {self.action} {self.entity_type} {self.entity_id}".strip()


class DataQualityCheck(models.Model):
    """One execution of a data-health check (PRD §8 reliability contract)."""

    STATUS_CHOICES = (
        ('ok', 'OK'),
        ('warn', 'Warning'),
        ('breach', 'Breach'),
    )

    id = models.CharField(
        primary_key=True, max_length=50, default=generate_custom_id, editable=False
    )
    check_name = models.CharField(max_length=100, db_index=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, db_index=True)
    expected = models.JSONField(default=dict, blank=True)
    observed = models.JSONField(default=dict, blank=True)
    details = models.JSONField(
        default=dict, blank=True,
        help_text="Drill-down evidence — e.g. unmatched references, stale metrics.",
    )
    window_start = models.DateTimeField(null=True, blank=True)
    window_end = models.DateTimeField(null=True, blank=True)
    checked_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ['-checked_at']
        indexes = [models.Index(fields=['check_name', '-checked_at'])]

    def __str__(self):
        return f"{self.check_name}: {self.status} ({self.checked_at:%Y-%m-%d %H:%M})"


class ReconciliationRun(models.Model):
    """Summary of one reconciliation sweep between canonical money records
    (``Payment``) and order records linked through ``Invoice``."""

    STATUS_CHOICES = (
        ('running', 'Running'),
        ('matched', 'Matched'),
        ('mismatch', 'Mismatch'),
        ('failed', 'Failed'),
    )

    id = models.CharField(
        primary_key=True, max_length=50, default=generate_custom_id, editable=False
    )
    name = models.CharField(max_length=100, default='payments_vs_orders')
    window_start = models.DateTimeField()
    window_end = models.DateTimeField()
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='running')
    summary = models.JSONField(
        default=dict, blank=True,
        help_text="Compared counts/totals per source, e.g. paid_payments vs paid_orders.",
    )
    exception_count = models.PositiveIntegerField(default=0)
    started_at = models.DateTimeField(default=timezone.now)
    finished_at = models.DateTimeField(null=True, blank=True)
    triggered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='reconciliation_runs',
    )
    error = models.TextField(blank=True, default='')

    class Meta:
        ordering = ['-started_at']

    def __str__(self):
        return f"{self.name} {self.window_start:%Y-%m-%d} -> {self.status}"


class ReconciliationException(models.Model):
    """A row that could not be matched during a reconciliation run.

    Open exceptions are the work queue for finance/ops — they never auto-
    resolve silently; a human marks them resolved with a note.
    """

    STATUS_CHOICES = (
        ('open', 'Open'),
        ('resolved', 'Resolved'),
    )

    id = models.CharField(
        primary_key=True, max_length=50, default=generate_custom_id, editable=False
    )
    run = models.ForeignKey(
        ReconciliationRun, on_delete=models.CASCADE, related_name='exceptions'
    )
    entity_type = models.CharField(max_length=50)   # e.g. 'payment', 'order'
    entity_id = models.CharField(max_length=100)    # payment reference / order id
    issue = models.CharField(max_length=100)        # e.g. 'payment_without_order'
    detail = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='open')
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='resolved_reconciliation_exceptions',
    )
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolution_note = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status', 'issue']),
            models.Index(fields=['entity_type', 'entity_id']),
        ]

    def __str__(self):
        return f"{self.issue}: {self.entity_type} {self.entity_id}"


class WorkItem(models.Model):
    """Unified work-queue row (PRD Phase 1 — operate safely).

    One canonical item per actionable condition, derived idempotently from
    source records (tickets, orders, products, designers, exceptions,
    withdrawals) by ``queues.sync_work_queues``. The (queue, entity_type,
    entity_id) triple is unique — re-syncing never duplicates an item and
    a manual close is never silently reopened.
    """

    QUEUE_CHOICES = (
        ('order_pending', 'Order — unacknowledged'),
        ('dispatch_late', 'Order — late to dispatch'),
        ('delivery_exception', 'Delivery exception'),
        ('support_case', 'Support case'),
        ('designer_onboarding', 'Designer onboarding'),
        ('catalog_moderation', 'Catalog moderation'),
        ('reconciliation', 'Reconciliation exception'),
        ('payout_approval', 'Payout approval'),
    )
    STATUS_CHOICES = (
        ('open', 'Open'),
        ('in_progress', 'In Progress'),
        ('resolved', 'Resolved'),
        ('closed', 'Closed'),
    )
    PRIORITY_CHOICES = (
        ('low', 'Low'),
        ('medium', 'Medium'),
        ('high', 'High'),
        ('urgent', 'Urgent'),
    )

    id = models.CharField(
        primary_key=True, max_length=50, default=generate_custom_id, editable=False
    )
    queue = models.CharField(max_length=40, choices=QUEUE_CHOICES, db_index=True)
    entity_type = models.CharField(max_length=100, db_index=True)
    entity_id = models.CharField(max_length=100, db_index=True)
    title = models.CharField(max_length=255)
    detail = models.JSONField(
        default=dict, blank=True,
        help_text="Snapshot evidence — order id, amount, age, source status.",
    )
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default='open', db_index=True
    )
    priority = models.CharField(
        max_length=10, choices=PRIORITY_CHOICES, default='medium', db_index=True
    )
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='work_items',
    )
    due_at = models.DateTimeField(null=True, blank=True, db_index=True)
    escalated = models.BooleanField(default=False)
    # Last source-derived state — lets sync detect when the source resolved
    # or changed without rewriting unrelated fields.
    source_status = models.CharField(max_length=50, blank=True, default='')
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='resolved_work_items',
    )
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolution_note = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['due_at', '-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['queue', 'entity_type', 'entity_id'],
                name='unique_work_item_per_source',
            ),
        ]
        indexes = [
            models.Index(fields=['status', 'queue', 'due_at']),
            models.Index(fields=['assigned_to', 'status']),
        ]

    @property
    def is_overdue(self):
        return (
            self.due_at is not None
            and self.status in ('open', 'in_progress')
            and self.due_at < timezone.now()
        )

    def __str__(self):
        return f"[{self.queue}] {self.title} ({self.status})"


class ApprovalRequest(models.Model):
    """Generic maker-checker record for high-impact actions (PRD §5).

    The *maker* creates a request recording the action + entity + payload
    snapshot; a different staff member (the *checker*) approves or rejects.
    The initiator can never approve their own request. Approvals execute
    the recorded action atomically and every step lands in AuditEvent.
    """

    STATUS_CHOICES = (
        ('pending', 'Pending'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
        ('executed', 'Executed'),
        ('cancelled', 'Cancelled'),
    )

    id = models.CharField(
        primary_key=True, max_length=50, default=generate_custom_id, editable=False
    )
    action = models.CharField(
        max_length=60, db_index=True,
        help_text="Capability-style action key, e.g. 'finance.payout_settled'.",
    )
    entity_type = models.CharField(max_length=100)
    entity_id = models.CharField(max_length=100)
    payload = models.JSONField(
        default=dict, blank=True,
        help_text="Snapshot of the action inputs the approver is signing off on.",
    )
    reason = models.TextField(blank=True, default='')
    status = models.CharField(
        max_length=12, choices=STATUS_CHOICES, default='pending', db_index=True
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, related_name='approval_requests_made',
    )
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='approval_requests_decided',
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['action', 'entity_type', 'entity_id'],
                condition=models.Q(status='pending'),
                name='unique_pending_approval_per_action',
            ),
        ]

    def __str__(self):
        return f"{self.action} {self.entity_type}:{self.entity_id} ({self.status})"
