from unittest.mock import patch
from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase
from rest_framework import status
from apps.designers.models import Designer, Notification
from apps.core.models import Product, Currency

User = get_user_model()


class AdminDesignerStatusTests(APITestCase):
    def setUp(self):
        self.admin_user = User.objects.create_superuser(
            email="admin@example.com", password="password123"
        )
        self.client.force_authenticate(user=self.admin_user)

        self.designer_user = User.objects.create_user(
            email="designer@example.com", username="designer1", password="password123"
        )
        self.designer = Designer.objects.create(
            user=self.designer_user,
            brand_name="Test Brand",
            status=Designer.Status.PENDING,
            bio="Maker of things",
            instagram="@testbrand",
            ships_internationally="yes",
        )
        self.currency = Currency.objects.create(name="USD", symbol="$")

    @patch("apps.administrator.views.resend_sendmail")
    def test_approve_designer_with_zero_products_succeeds(self, mock_mail):
        """Approval is no longer gated on product count — zero-product
        designers can be approved and are nudged to upload instead."""
        url = f"/manage/designers/{self.designer.id}/update-status"
        response = self.client.patch(url, {"status": "approved"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.designer.refresh_from_db()
        self.assertEqual(self.designer.status, Designer.Status.APPROVED)
        self.assertTrue(self.designer.is_verified)

    @patch("apps.administrator.views.resend_sendmail")
    def test_approve_designer_with_one_product_succeeds(self, mock_mail):
        Product.objects.create(
            user=self.designer_user,
            name="Test Dress",
            price=100.0,
            currency=self.currency,
        )
        url = f"/manage/designers/{self.designer.id}/update-status"
        response = self.client.patch(url, {"status": "approved"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.designer.refresh_from_db()
        self.assertEqual(self.designer.status, Designer.Status.APPROVED)
        self.assertTrue(self.designer.is_verified)

    @patch("apps.administrator.views.resend_sendmail")
    def test_reject_designer_with_zero_products_succeeds(self, mock_mail):
        url = f"/manage/designers/{self.designer.id}/update-status"
        response = self.client.patch(url, {"status": "rejected"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.designer.refresh_from_db()
        self.assertEqual(self.designer.status, Designer.Status.REJECTED)

    @patch("apps.administrator.views.resend_sendmail")
    def test_invalid_status_rejected(self, mock_mail):
        url = f"/manage/designers/{self.designer.id}/update-status"
        response = self.client.patch(url, {"status": "bogus"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        self.designer.refresh_from_db()
        self.assertEqual(self.designer.status, Designer.Status.PENDING)

    @patch("apps.administrator.views.resend_sendmail")
    def test_partial_update_status_triggers_notifications(self, mock_mail):
        """The admin UI PATCHes the resource directly (partial_update) —
        status transitions there must fire the same notification + email
        as the update-status action."""
        url = f"/manage/designers/{self.designer.id}"
        response = self.client.patch(
            url,
            {"status": "approved", "status_reasons": ["Brand authenticity verified"]},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.designer.refresh_from_db()
        self.assertEqual(self.designer.status, Designer.Status.APPROVED)
        self.assertTrue(self.designer.is_verified)

        self.assertTrue(
            Notification.objects.filter(
                user=self.designer_user, title="Profile approved"
            ).exists()
        )

    @patch("apps.administrator.views.resend_sendmail")
    def test_partial_update_without_status_change_sends_no_notification(self, mock_mail):
        url = f"/manage/designers/{self.designer.id}"
        response = self.client.patch(
            url, {"city": "Lagos"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(
            Notification.objects.filter(user=self.designer_user).exists()
        )


# =====================================================
# PHASE 0 — audit, capabilities, reconciliation, health
# =====================================================

from decimal import Decimal

from django.test import TestCase

from apps.administrator.audit import record_audit
from apps.administrator.capabilities import capabilities_for, has_capability
from apps.administrator.checks import (
    check_dead_letter_backlog,
    reconcile_payments_vs_orders,
)
from apps.administrator.models import (
    ApprovalRequest, AuditEvent, CapabilityGrant, Case, DataQualityCheck,
    PolicyVersion, ReconciliationException, ReconciliationRun,
)
from apps.customers.models import Customer, Order, OrderItem
from apps.pay.models import Escrow, Invoice, Payment


def _admin(email, role):
    user = User.objects.create_user(email=email, password="password123")
    user.user_type = "admin"
    user.admin_role = role
    user.is_staff = True  # real role grants set is_staff (auth views)
    user.save(update_fields=["user_type", "admin_role", "is_staff"])
    return user


def _paid_order(user, *, amount="100.00", qty=2, paid=True):
    """Customer + Payment + Invoice + Order + OrderItem, optionally unpaid."""
    customer, _ = Customer.objects.get_or_create(user=user)
    payment = Payment.objects.create(
        user=user, amount=Decimal(amount) * qty,
        payment_method="card",
        status="success" if paid else "pending",
        is_paid=paid,
    )
    invoice = Invoice.objects.create(
        user=user, payment=payment,
        amount=int(Decimal(amount) * qty),
    )
    order = Order.objects.create(
        customer=customer, invoice=invoice,
        total_amount=Decimal(amount) * qty,
        sub_total=Decimal(amount) * qty,
    )
    OrderItem.objects.create(
        order=order, amount=Decimal(amount), quantity=qty,
        sub_total=Decimal(amount) * qty,
    )
    return order, payment


class AuditEventTests(TestCase):
    def test_append_only_blocks_update_and_delete(self):
        event = AuditEvent.objects.create(action="test.action")
        event.reason = "mutated"
        with self.assertRaises(ValueError):
            event.save()
        with self.assertRaises(ValueError):
            event.delete()
        event.refresh_from_db()
        self.assertNotEqual(event.reason, "mutated")

    def test_record_audit_captures_context(self):
        user = _admin("m@x.com", "marketer")
        event = record_audit(
            actor=user, action="lead.qualify",
            entity_type="DesignerLead", entity_id="L1",
            before={"status": "Needs Review"}, after={"status": "Qualified"},
            reason="evidence ok",
        )
        self.assertEqual(event.actor_email, "m@x.com")
        self.assertEqual(event.actor_role, "marketer")
        self.assertEqual(event.entity_id, "L1")
        self.assertEqual(event.before["status"], "Needs Review")


class CapabilityTests(APITestCase):
    def test_capability_matrix(self):
        marketer = _admin("mk@x.com", "marketer")
        exec_ = _admin("ceo@x.com", "c_level")
        self.assertTrue(has_capability(marketer, "marketing.send"))
        self.assertTrue(has_capability(exec_, "marketing.view"))
        self.assertFalse(has_capability(exec_, "marketing.send"))
        self.assertFalse(has_capability(exec_, "finance.approve_payout"))
        self.assertTrue(has_capability(exec_, "audit.view"))
        self.assertFalse(has_capability(marketer, "audit.view"))

    def test_denied_capability_writes_audit_event(self):
        exec_ = _admin("ceo@x.com", "c_level")
        self.client.force_authenticate(exec_)
        res = self.client.post(
            "/marketing/leads/send_broadcast/",
            {"html_body": "<p>x</p>"}, format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
        self.assertTrue(
            AuditEvent.objects.filter(
                action="permission.denied",
                entity_id="marketing.send",
                actor_email="ceo@x.com",
            ).exists()
        )

    def test_exec_can_read_but_not_mutate_marketing(self):
        exec_ = _admin("ceo@x.com", "c_level")
        self.client.force_authenticate(exec_)
        self.assertEqual(self.client.get("/marketing/leads/").status_code, 200)
        from apps.marketing.models import DesignerLead
        lead = DesignerLead.objects.create(brand_name="B", email="b@x.com")
        res = self.client.post(f"/marketing/leads/{lead.id}/qualify/", {}, format="json")
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_audit_events_endpoint_requires_audit_view(self):
        marketer = _admin("mk@x.com", "marketer")
        self.client.force_authenticate(marketer)
        self.assertEqual(
            self.client.get("/manage/audit-events").status_code,
            status.HTTP_403_FORBIDDEN,
        )
        exec_ = _admin("ceo@x.com", "c_level")
        self.client.force_authenticate(exec_)
        self.assertEqual(self.client.get("/manage/audit-events").status_code, 200)


class ReconciliationTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="cust@x.com", password="password123"
        )

    def test_matched_when_payment_has_order(self):
        _paid_order(self.user)
        run = reconcile_payments_vs_orders(days=1)
        self.assertEqual(run.status, "matched")
        self.assertEqual(run.exception_count, 0)
        self.assertEqual(run.summary["paid_payments"], 1)
        self.assertEqual(run.summary["orders_in_window"], 1)

    def test_payment_without_order_creates_exception(self):
        Payment.objects.create(
            user=self.user, amount=Decimal("50"), payment_method="card",
            status="success", is_paid=True,
        )
        run = reconcile_payments_vs_orders(days=1)
        self.assertEqual(run.status, "mismatch")
        exc = ReconciliationException.objects.get(run=run)
        self.assertEqual(exc.issue, "payment_without_order")
        self.assertEqual(exc.status, "open")

    def test_order_without_paid_payment_flagged(self):
        _paid_order(self.user, paid=False)
        run = reconcile_payments_vs_orders(days=1)
        self.assertEqual(run.status, "mismatch")
        exc = ReconciliationException.objects.get(run=run)
        self.assertEqual(exc.issue, "order_without_paid_payment")

    def test_resolve_exception_endpoint(self):
        Payment.objects.create(
            user=self.user, amount=Decimal("50"), payment_method="card",
            status="success", is_paid=True,
        )
        run = reconcile_payments_vs_orders(days=1)
        exc = ReconciliationException.objects.get(run=run)

        admin = User.objects.create_superuser(
            email="root@x.com", password="password123"
        )
        self.client.force_authenticate(admin)
        res = self.client.post(
            f"/manage/reconciliation-exceptions/{exc.id}/resolve",
            {"note": "manual match confirmed"}, format="json",
        )
        self.assertEqual(res.status_code, 200, res.data)
        exc.refresh_from_db()
        self.assertEqual(exc.status, "resolved")
        self.assertEqual(exc.resolved_by, admin)
        self.assertTrue(
            AuditEvent.objects.filter(
                action="reconciliation.resolve", entity_id=exc.id
            ).exists()
        )


class DataHealthTests(APITestCase):
    def test_dead_letter_ok_when_empty(self):
        self.assertEqual(check_dead_letter_backlog().status, "ok")

    def test_dead_letter_warns_on_backlog(self):
        from apps.analytics.models import DeadLetterEvent
        DeadLetterEvent.objects.create(payload={"bad": 1}, reason="malformed")
        check = check_dead_letter_backlog()
        self.assertEqual(check.status, "warn")
        self.assertEqual(check.observed["count"], 1)

    def test_run_endpoint(self):
        admin = User.objects.create_superuser(
            email="root@x.com", password="password123"
        )
        self.client.force_authenticate(admin)
        res = self.client.post("/manage/data-quality/run", {"days": 1}, format="json")
        self.assertEqual(res.status_code, status.HTTP_202_ACCEPTED, res.data)
        self.assertTrue(DataQualityCheck.objects.filter(
            check_name="payments_vs_orders").exists())

    def test_run_endpoint_denies_marketer(self):
        marketer = _admin("mk@x.com", "marketer")
        self.client.force_authenticate(marketer)
        res = self.client.post("/manage/data-quality/run", {}, format="json")
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
        # ...but a marketer CAN read the results.
        self.assertEqual(self.client.get("/manage/data-quality").status_code, 200)


class DashboardIntegrityTests(APITestCase):
    def setUp(self):
        self.exec_ = _admin("ceo@x.com", "c_level")
        self.client.force_authenticate(self.exec_)
        self.customer_user = User.objects.create_user(
            email="cust@x.com", password="password123"
        )
        self.designer_user = User.objects.create_user(
            email="des@x.com", password="password123"
        )

    def test_meta_block_and_no_fabricated_kpis(self):
        order, payment = _paid_order(self.customer_user)
        Escrow.objects.create(
            payment=payment, customer=self.customer_user,
            designer=self.designer_user, amount=Decimal("200"),
            platform_commission=Decimal("20"), status="held",
        )
        res = self.client.get("/manage/c-level-dashboard")
        self.assertEqual(res.status_code, 200)
        data = res.data

        # Meta block: definitions, sources, generated_at, reconciliation.
        self.assertIn("definitions", data["meta"])
        self.assertIn("sources", data["meta"])
        self.assertIn("generated_at", data["meta"])
        self.assertIn("reconciliation", data["meta"])

        # GMV counts the paid item's sub_total (2 x 100 = 200)...
        self.assertEqual(data["financials"]["total_gmv"], 200.0)
        # ...held escrow is NOT a payout — it shows as held liability.
        self.assertEqual(data["financials"]["total_payouts"], 0.0)
        self.assertEqual(data["financials"]["held_escrow"], 200.0)
        self.assertEqual(data["financials"]["commission_recognized"], 0.0)

    def test_unpaid_items_excluded_from_gmv(self):
        _paid_order(self.customer_user, paid=False)
        res = self.client.get("/manage/c-level-dashboard")
        self.assertEqual(res.data["financials"]["total_gmv"], 0.0)


# =====================================================
# PHASE 1 — unified work queue + order timeline
# =====================================================

from datetime import timedelta

from django.utils import timezone

from apps.administrator.models import WorkItem
from apps.administrator.queues import sync_work_queues
from apps.core.models import SupportTicket
from apps.pay.models import Wallet, Withdrawal


class WorkQueueDerivationTests(TestCase):
    def test_ticket_creates_support_item_idempotently(self):
        ticket = SupportTicket.objects.create(
            subject="Where is my order?", description="help", priority="high",
        )
        stats = sync_work_queues()
        item = WorkItem.objects.get(queue="support_case")
        self.assertEqual(item.entity_type, "SupportTicket")
        self.assertEqual(item.entity_id, str(ticket.id))
        self.assertEqual(item.status, "open")
        self.assertEqual(item.priority, "high")
        self.assertIsNotNone(item.due_at)
        self.assertEqual(stats["created"], 1)

        # Re-running never duplicates.
        stats2 = sync_work_queues()
        self.assertEqual(WorkItem.objects.filter(queue="support_case").count(), 1)
        self.assertEqual(stats2["created"], 0)

    def test_resolved_ticket_auto_closes_item(self):
        ticket = SupportTicket.objects.create(subject="s", description="d")
        sync_work_queues()
        ticket.status = "resolved"
        ticket.save()
        sync_work_queues()
        item = WorkItem.objects.get(queue="support_case")
        self.assertEqual(item.status, "closed")
        self.assertEqual(item.resolution_note, "source resolved")
        self.assertIsNotNone(item.resolved_at)

    def test_human_resolution_is_never_auto_reopened(self):
        SupportTicket.objects.create(subject="s", description="d")
        sync_work_queues()
        item = WorkItem.objects.get(queue="support_case")
        item.status = "resolved"
        item.save()
        sync_work_queues()
        item.refresh_from_db()
        self.assertEqual(item.status, "resolved")

    def test_pending_designer_creates_onboarding_item(self):
        user = User.objects.create_user(email="nd@x.com", password="p")
        designer = Designer.objects.create(
            user=user, brand_name="New Brand", status=Designer.Status.PENDING,
        )
        sync_work_queues()
        item = WorkItem.objects.get(queue="designer_onboarding")
        self.assertEqual(item.entity_id, str(designer.id))
        self.assertIn("New Brand", item.title)

    def test_unpublished_product_creates_moderation_item(self):
        product = Product.objects.create(
            name="Draft Dress", description="d", price=50,
            is_published=False, is_active=True,
        )
        sync_work_queues()
        item = WorkItem.objects.get(queue="catalog_moderation")
        self.assertEqual(item.entity_id, str(product.id))

    def test_pending_withdrawal_creates_payout_item(self):
        user = User.objects.create_user(email="w@x.com", password="p")
        wallet = Wallet.objects.create(user=user, available_balance=Decimal("500"))
        withdrawal = Withdrawal.objects.create(
            wallet=wallet, user=user, amount=Decimal("100"),
            status="pending", reference="WD-1", bank_name="Bank",
            bank_code="001", account_number="123", account_name="Acct",
        )
        sync_work_queues()
        item = WorkItem.objects.get(queue="payout_approval")
        self.assertEqual(item.entity_id, str(withdrawal.id))

    def test_open_recon_exception_creates_item(self):
        run = ReconciliationRun.objects.create(
            window_start=timezone.now() - timedelta(days=1),
            window_end=timezone.now(), status="mismatch",
        )
        exc = ReconciliationException.objects.create(
            run=run, issue="payment_without_order",
            entity_type="Payment", entity_id="P-1",
        )
        sync_work_queues()
        item = WorkItem.objects.get(queue="reconciliation")
        self.assertEqual(item.entity_id, str(exc.id))
        exc.status = "resolved"
        exc.save()
        sync_work_queues()
        item.refresh_from_db()
        self.assertEqual(item.status, "closed")

    def test_stale_paid_pending_order_creates_item(self):
        user = User.objects.create_user(email="oc@x.com", password="p")
        order, _ = _paid_order(user)
        Order.objects.filter(pk=order.pk).update(
            created_at=timezone.now() - timedelta(hours=30),
        )
        sync_work_queues()
        item = WorkItem.objects.get(queue="order_pending")
        self.assertEqual(item.entity_id, order.order_id)
        self.assertEqual(item.priority, "high")


class WorkItemApiTests(APITestCase):
    def setUp(self):
        self.agent = _admin("agent@x.com", "support_agent")
        self.exec_ = _admin("ceo@x.com", "c_level")
        self.item = WorkItem.objects.create(
            queue="support_case", entity_type="SupportTicket", entity_id="T1",
            title="TKT-1 — help", priority="medium",
            due_at=timezone.now() - timedelta(hours=1),
        )

    def test_list_requires_work_view(self):
        self.client.force_authenticate(self.agent)
        self.assertEqual(self.client.get("/manage/work-items").status_code, 200)
        outsider = User.objects.create_user(email="n@x.com", password="p")
        self.client.force_authenticate(outsider)
        self.assertEqual(
            self.client.get("/manage/work-items").status_code,
            status.HTTP_403_FORBIDDEN,
        )

    def test_exec_can_view_but_not_manage(self):
        self.client.force_authenticate(self.exec_)
        self.assertEqual(self.client.get("/manage/work-items").status_code, 200)
        res = self.client.post(f"/manage/work-items/{self.item.id}/assign")
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
        self.assertTrue(
            AuditEvent.objects.filter(
                action="permission.denied", entity_id="work.manage",
            ).exists()
        )

    def test_assign_self_claims_and_starts(self):
        self.client.force_authenticate(self.agent)
        res = self.client.post(f"/manage/work-items/{self.item.id}/assign")
        self.assertEqual(res.status_code, 200, res.data)
        self.item.refresh_from_db()
        self.assertEqual(self.item.assigned_to, self.agent)
        self.assertEqual(self.item.status, "in_progress")
        self.assertTrue(
            AuditEvent.objects.filter(
                action="work.assign", entity_id=self.item.id,
            ).exists()
        )

    def test_start_resolve_reopen_flow(self):
        self.client.force_authenticate(self.agent)
        res = self.client.post(f"/manage/work-items/{self.item.id}/start")
        self.assertEqual(res.status_code, 200, res.data)
        self.item.refresh_from_db()
        self.assertEqual(self.item.status, "in_progress")
        self.assertEqual(self.item.assigned_to, self.agent)

        res = self.client.post(
            f"/manage/work-items/{self.item.id}/resolve",
            {"note": "ticket handled"}, format="json",
        )
        self.assertEqual(res.status_code, 200, res.data)
        self.item.refresh_from_db()
        self.assertEqual(self.item.status, "resolved")
        self.assertEqual(self.item.resolved_by, self.agent)
        self.assertEqual(self.item.resolution_note, "ticket handled")

        res = self.client.post(f"/manage/work-items/{self.item.id}/reopen")
        self.assertEqual(res.status_code, 200, res.data)
        self.item.refresh_from_db()
        self.assertEqual(self.item.status, "open")
        self.assertIsNone(self.item.resolved_at)

    def test_close_and_priority_and_escalate(self):
        self.client.force_authenticate(self.agent)
        res = self.client.post(
            f"/manage/work-items/{self.item.id}/priority",
            {"priority": "urgent"}, format="json",
        )
        self.assertEqual(res.status_code, 200, res.data)
        res = self.client.post(
            f"/manage/work-items/{self.item.id}/escalate",
            {"reason": "sla breached"}, format="json",
        )
        self.assertEqual(res.status_code, 200, res.data)
        res = self.client.post(
            f"/manage/work-items/{self.item.id}/close",
            {"note": "not actionable"}, format="json",
        )
        self.assertEqual(res.status_code, 200, res.data)
        self.item.refresh_from_db()
        self.assertEqual(self.item.status, "closed")
        self.assertTrue(self.item.escalated)
        self.assertEqual(self.item.priority, "urgent")

    def test_summary_and_filters(self):
        WorkItem.objects.create(
            queue="reconciliation", entity_type="ReconciliationException",
            entity_id="E1", title="mismatch",
        )
        self.client.force_authenticate(self.agent)
        res = self.client.get("/manage/work-items/summary")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["total_open"], 2)
        self.assertEqual(res.data["overdue"], 1)
        self.assertEqual(res.data["by_queue"]["support_case"], 1)

        self.client.post(f"/manage/work-items/{self.item.id}/assign")
        res = self.client.get("/manage/work-items?mine=true")
        self.assertEqual(len(res.data["results"]), 1)
        res = self.client.get("/manage/work-items?overdue=true")
        self.assertEqual(len(res.data["results"]), 1)
        res = self.client.get("/manage/work-items?queue=reconciliation")
        self.assertEqual(len(res.data["results"]), 1)

    def test_sync_endpoint_derives_items(self):
        SupportTicket.objects.create(subject="s", description="d")
        self.client.force_authenticate(self.agent)
        res = self.client.post("/manage/work-items/sync")
        self.assertEqual(res.status_code, status.HTTP_202_ACCEPTED, res.data)
        self.assertTrue(WorkItem.objects.filter(queue="support_case").exists())


class OrderTimelineTests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            email="root@x.com", password="password123",
        )
        self.client.force_authenticate(self.admin)
        self.customer_user = User.objects.create_user(
            email="tlc@x.com", password="p",
        )

    def test_timeline_aggregates_order_payment_items(self):
        order, payment = _paid_order(self.customer_user)
        res = self.client.get(f"/manage/orders/{order.pk}/timeline")
        self.assertEqual(res.status_code, 200, res.data)
        types = [e["type"] for e in res.data["events"]]
        self.assertIn("order.created", types)
        self.assertIn("payment.attempt", types)
        self.assertIn("item.created", types)
        stamps = [e["at"] for e in res.data["events"]]
        self.assertEqual(stamps, sorted(stamps))
        self.assertNotIn("processor_payment_id", str(res.data))

    def test_timeline_includes_returns_and_audit(self):
        from apps.customers.models import ReturnRequest
        order, _ = _paid_order(self.customer_user)
        item = order.items.first()
        ReturnRequest.objects.create(order_item=item, reason="damaged")
        AuditEvent.objects.create(
            action="order.refund_approved", actor_email="root@x.com",
            entity_type="Order", entity_id=order.order_id,
        )
        res = self.client.get(f"/manage/orders/{order.pk}/timeline")
        types = [e["type"] for e in res.data["events"]]
        self.assertIn("return.requested", types)
        self.assertIn("audit", types)
        audit_ev = next(e for e in res.data["events"] if e["type"] == "audit")
        self.assertIn("order.refund_approved", audit_ev["summary"])


# =====================================================
# PHASE 1 — role enforcement on operational endpoints
# =====================================================


class RoleEnforcementTests(APITestCase):
    """Named capabilities gate the admin mutation surface — the rights
    table in code: catalog owns publish, support requests (not issues)
    refunds, payouts need finance capability + settlement evidence."""

    def setUp(self):
        self.pm = _admin("pm@x.com", "product_manager")
        self.agent = _admin("sa@x.com", "support_agent")
        self.exec_ = _admin("ceo@x.com", "c_level")
        self.designer_user = User.objects.create_user(
            email="des@x.com", password="p",
        )
        self.designer = Designer.objects.create(
            user=self.designer_user, brand_name="Brand",
            status=Designer.Status.PENDING,
            bio="Bio", instagram="@brand", ships_internationally="yes",
        )
        self.product = Product.objects.create(
            user=self.designer_user, name="Dress", description="d",
            price=50, is_published=True, is_admin_published=False,
        )

    def test_product_manager_can_publish(self):
        self.client.force_authenticate(self.pm)
        res = self.client.patch(
            f"/manage/products/{self.product.id}/publish",
            {"exception_reason": "launch partner onboarding"},
            format="json",
        )
        self.assertEqual(res.status_code, 200, res.data)
        self.product.refresh_from_db()
        self.assertTrue(self.product.is_admin_published)
        self.assertTrue(
            AuditEvent.objects.filter(
                action="catalog.publish", entity_id=str(self.product.id),
            ).exists()
        )

    def test_support_agent_can_view_but_not_publish(self):
        self.client.force_authenticate(self.agent)
        self.assertEqual(self.client.get("/manage/products").status_code, 200)
        res = self.client.patch(
            f"/manage/products/{self.product.id}/publish", {}, format="json"
        )
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
        self.assertTrue(
            AuditEvent.objects.filter(
                action="permission.denied", entity_id="catalog.publish",
                actor_email="sa@x.com",
            ).exists()
        )

    def test_exec_reads_orders_but_cannot_mutate(self):
        user = User.objects.create_user(email="c@x.com", password="p")
        order, _ = _paid_order(user)
        self.client.force_authenticate(self.exec_)
        self.assertEqual(self.client.get("/manage/orders").status_code, 200)
        self.assertEqual(
            self.client.get(f"/manage/orders/{order.pk}/timeline").status_code,
            200,
        )
        res = self.client.patch(
            f"/manage/orders/{order.pk}", {"status": "cancelled"},
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_support_manages_tickets_and_audit_written(self):
        ticket = SupportTicket.objects.create(subject="s", description="d")
        self.client.force_authenticate(self.agent)
        res = self.client.post(
            f"/manage/tickets/{ticket.id}/status",
            {"status": "in_progress", "reason": "picked up case"},
            format="json",
        )
        self.assertEqual(res.status_code, 200, res.data)
        self.assertTrue(
            AuditEvent.objects.filter(
                action="support.ticket_status", entity_id=str(ticket.id),
            ).exists()
        )

        # A product manager cannot touch support tickets.
        self.client.force_authenticate(self.pm)
        res = self.client.post(
            f"/manage/tickets/{ticket.id}/status",
            {"status": "resolved"}, format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_dispute_refund_requires_finance_capability(self):
        from apps.customers.models import Dispute, ReturnRequest
        user = User.objects.create_user(email="dc@x.com", password="p")
        order, _ = _paid_order(user)
        item = order.items.first()
        product = Product.objects.create(
            user=self.designer_user, name="P2", description="d", price=10,
        )
        item.product = product
        item.save()
        rr = ReturnRequest.objects.create(order_item=item, reason="damaged")
        dispute = Dispute.objects.create(
            return_request=rr, opened_by=user,
        )

        # Support can resolve without a refund.
        self.client.force_authenticate(self.agent)
        res = self.client.post(
            f"/manage/disputes/{dispute.dispute_id}/resolve",
            {"resolution": "refund_denied"}, format="json",
        )
        self.assertEqual(res.status_code, 200, res.data)

        # But resolving WITH a refund is finance-only ("request only" for
        # support per the rights table).
        dispute.status = "opened"
        dispute.resolution = None
        dispute.save()
        res = self.client.post(
            f"/manage/disputes/{dispute.dispute_id}/resolve",
            {"resolution": "refund_approved", "refund_amount": "25.00"},
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
        dispute.refresh_from_db()
        self.assertEqual(dispute.status, "opened")
        self.assertTrue(
            AuditEvent.objects.filter(
                action="permission.denied", entity_id="finance.refund",
            ).exists()
        )

        # Superadmin (holds *) can issue it — audited.
        root = User.objects.create_superuser(
            email="root@x.com", password="p",
        )
        self.client.force_authenticate(root)
        res = self.client.post(
            f"/manage/disputes/{dispute.dispute_id}/resolve",
            {"resolution": "refund_approved", "refund_amount": "25.00"},
            format="json",
        )
        self.assertEqual(res.status_code, 200, res.data)
        self.assertTrue(
            AuditEvent.objects.filter(
                action="support.dispute_resolve",
                entity_id=str(dispute.id),
            ).exists()
        )

    def test_withdrawal_completion_needs_finance_and_evidence(self):
        user = User.objects.create_user(email="wd@x.com", password="p")
        wallet = Wallet.objects.create(
            user=user, available_balance=Decimal("500"),
        )
        withdrawal = Withdrawal.objects.create(
            wallet=wallet, user=user, amount=Decimal("100"),
            status="pending", reference="WD-2", bank_name="B",
            bank_code="001", account_number="1", account_name="A",
        )

        # Support agents can't even list payouts (no finance.view).
        self.client.force_authenticate(self.agent)
        self.assertEqual(
            self.client.get("/manage/withdrawals").status_code,
            status.HTTP_403_FORBIDDEN,
        )

        root = User.objects.create_superuser(
            email="root2@x.com", password="p",
        )
        self.client.force_authenticate(root)

        # No settlement reference -> rejected (FIN-03: not cosmetic).
        res = self.client.post(
            f"/manage/withdrawals/{withdrawal.id}/mark_completed",
            {}, format="json",
        )
        self.assertEqual(res.status_code, 400)
        withdrawal.refresh_from_db()
        self.assertEqual(withdrawal.status, "pending")

        # With evidence -> settles, recorded.
        res = self.client.post(
            f"/manage/withdrawals/{withdrawal.id}/mark_completed",
            {"settlement_reference": "FLW-TX-999"}, format="json",
        )
        self.assertEqual(res.status_code, 200, res.data)
        withdrawal.refresh_from_db()
        self.assertEqual(withdrawal.status, "completed")
        self.assertEqual(
            withdrawal.flutterwave_transfer_id, "FLW-TX-999",
        )
        self.assertTrue(
            AuditEvent.objects.filter(
                action="finance.payout_settled",
                entity_id=str(withdrawal.id),
            ).exists()
        )

    def test_designer_status_change_audited(self):
        designer = self.designer
        self.client.force_authenticate(self.agent)
        res = self.client.patch(
            f"/manage/designers/{designer.id}/update-status",
            {"status": "approved",
             "status_reasons": ["portfolio verified"]},
            format="json",
        )
        self.assertEqual(res.status_code, 200, res.data)
        event = AuditEvent.objects.get(
            action="designer.status_change", entity_id=str(designer.id),
        )
        self.assertEqual(event.before["status"], "pending")
        self.assertEqual(event.after["status"], "approved")

        # Product managers see designers but cannot approve them.
        designer.status = Designer.Status.PENDING
        designer.save()
        self.client.force_authenticate(self.pm)
        self.assertEqual(
            self.client.get("/manage/designers").status_code, 200,
        )
        res = self.client.patch(
            f"/manage/designers/{designer.id}/update-status",
            {"status": "approved"}, format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)


class AdminApprovalAndGateTests(APITestCase):
    """Maker-checker payouts, DES-02/CAT-01 evidence gates,
    capability-filtered global search."""

    def setUp(self):
        self.maker = User.objects.create_superuser(
            email="maker@example.com", password="password123")
        self.checker = User.objects.create_superuser(
            email="checker@example.com", password="password123")
        self.pm = _admin("pm2@example.com", "product_manager")
        self.marketer = _admin("mk@example.com", "marketer")
        self.designer_user = User.objects.create_user(
            email="dg@example.com", username="dg", password="password123")
        self.wallet = Wallet.objects.create(user=self.designer_user)

    def _withdrawal(self, amount):
        return Withdrawal.objects.create(
            wallet=self.wallet, user=self.designer_user,
            amount=Decimal(str(amount)), reference=f"W-{amount}",
            bank_name="Bank", bank_code="001",
            account_number="123", account_name="Name",
        )

    def test_payout_below_threshold_settles_directly(self):
        w = self._withdrawal(100)
        self.client.force_authenticate(self.maker)
        res = self.client.post(
            f"/manage/withdrawals/{w.id}/mark_completed",
            {"settlement_reference": "tx-1"}, format="json")
        self.assertEqual(res.status_code, 200, res.data)
        w.refresh_from_db()
        self.assertEqual(w.status, "completed")
        self.assertEqual(w.flutterwave_transfer_id, "tx-1")

    def test_payout_above_threshold_requires_second_approver(self):
        w = self._withdrawal(5000)
        self.client.force_authenticate(self.maker)
        res = self.client.post(
            f"/manage/withdrawals/{w.id}/mark_completed",
            {"settlement_reference": "tx-2"}, format="json")
        self.assertEqual(res.status_code, 202, res.data)
        self.assertEqual(res.data["status"], "approval_required")
        w.refresh_from_db()
        self.assertEqual(w.status, "pending")
        approval = ApprovalRequest.objects.get(id=res.data["approval_id"])
        self.assertEqual(approval.requested_by, self.maker)

        # The maker can never be the checker.
        res = self.client.post(f"/manage/approvals/{approval.id}/approve")
        self.assertEqual(res.status_code, 403)

        # A different finance-capable approver executes the settlement.
        self.client.force_authenticate(self.checker)
        res = self.client.post(f"/manage/approvals/{approval.id}/approve")
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data["status"], "executed")
        w.refresh_from_db()
        self.assertEqual(w.status, "completed")
        self.assertEqual(w.flutterwave_transfer_id, "tx-2")
        self.assertTrue(AuditEvent.objects.filter(
            action="approval.requested").exists())
        self.assertTrue(AuditEvent.objects.filter(
            action="approval.approved").exists())

    def test_approval_reject_keeps_withdrawal_pending(self):
        w = self._withdrawal(8000)
        self.client.force_authenticate(self.maker)
        res = self.client.post(
            f"/manage/withdrawals/{w.id}/mark_completed",
            {"settlement_reference": "tx-3"}, format="json")
        approval_id = res.data["approval_id"]

        self.client.force_authenticate(self.checker)
        res = self.client.post(
            f"/manage/approvals/{approval_id}/reject",
            {"note": "insufficient proof"}, format="json")
        self.assertEqual(res.status_code, 200, res.data)
        w.refresh_from_db()
        self.assertEqual(w.status, "pending")
        self.assertEqual(
            ApprovalRequest.objects.get(id=approval_id).status, "rejected")

    def test_nonfinance_cannot_see_approvals(self):
        self.client.force_authenticate(self.pm)
        res = self.client.get("/manage/approvals")
        self.assertEqual(res.status_code, 403)

    def test_designer_approval_requires_evidence_or_exception(self):
        designer = Designer.objects.create(
            user=User.objects.create_user(
                email="bare@example.com", username="bare",
                password="password123"),
            brand_name="", status=Designer.Status.PENDING)
        url = f"/manage/designers/{designer.id}/update-status"
        self.client.force_authenticate(self.maker)
        res = self.client.patch(url, {"status": "approved"}, format="json")
        self.assertEqual(res.status_code, 400, res.data)
        self.assertIn("checks", res.data)
        designer.refresh_from_db()
        self.assertEqual(designer.status, Designer.Status.PENDING)

        res = self.client.patch(
            url, {"status": "approved",
                  "status_reasons": ["verified via offline call"]},
            format="json")
        self.assertEqual(res.status_code, 200, res.data)
        designer.refresh_from_db()
        self.assertEqual(designer.status, Designer.Status.APPROVED)

    def test_product_publish_requires_moderation_or_exception(self):
        product = Product.objects.create(
            user=self.designer_user, name="Bare", description="",
            price=50, is_published=True, is_admin_published=False)
        url = f"/manage/products/{product.id}/publish"
        self.client.force_authenticate(self.pm)
        res = self.client.patch(url, {}, format="json")
        self.assertEqual(res.status_code, 400, res.data)
        product.refresh_from_db()
        self.assertFalse(product.is_admin_published)

        res = self.client.patch(
            url, {"exception_reason": "launch partner onboarding"},
            format="json")
        self.assertEqual(res.status_code, 200, res.data)
        product.refresh_from_db()
        self.assertTrue(product.is_admin_published)
        self.assertTrue(AuditEvent.objects.filter(
            action="catalog.publish",
            entity_id=str(product.id)).exists())

    def test_global_search_filtered_by_capability(self):
        Designer.objects.create(
            user=User.objects.create_user(
                email="fd@example.com", username="fd",
                password="password123"),
            brand_name="Findme Brand", status=Designer.Status.PENDING)
        Product.objects.create(
            user=self.designer_user, name="Findme Dress", price=10)
        SupportTicket.objects.create(
            subject="Findme ticket", description="d")

        # pm: catalog + designers + orders, but NOT support/customers.
        self.client.force_authenticate(self.pm)
        res = self.client.get("/manage/search", {"q": "Findme"})
        self.assertEqual(res.status_code, 200, res.data)
        types = {r["type"] for r in res.data["results"]}
        self.assertIn("product", types)
        self.assertIn("designer", types)
        self.assertNotIn("ticket", types)

        # Marketer has no domain-view caps: empty result set.
        self.client.force_authenticate(self.marketer)
        res = self.client.get("/manage/search", {"q": "Findme"})
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data["results"], [])

        # Superadmin sees every domain.
        self.client.force_authenticate(self.maker)
        res = self.client.get("/manage/search", {"q": "Findme"})
        types = {r["type"] for r in res.data["results"]}
        self.assertIn("ticket", types)
        self.assertIn("designer", types)
        self.assertIn("product", types)


class AdminCaseLifecycleTests(APITestCase):
    """SUP-02 transition map, reason/evidence recording, finance role,
    gate checklist surfaces."""

    def setUp(self):
        self.admin = User.objects.create_superuser(
            email="sadmin@example.com", password="password123")
        self.agent = _admin("sa@example.com", "support_agent")
        self.pm = _admin("pm3@example.com", "product_manager")
        self.fin_maker = _admin("fin1@example.com", "finance")
        self.fin_checker = _admin("fin2@example.com", "finance")
        self.owner = User.objects.create_user(
            email="owner@example.com", username="owner",
            password="password123")

    def _ticket(self, **kw):
        return SupportTicket.objects.create(
            subject="case", description="d", **kw)

    def test_reason_required_for_transition(self):
        ticket = self._ticket()
        self.client.force_authenticate(self.agent)
        res = self.client.post(
            f"/manage/tickets/{ticket.id}/status",
            {"status": "triaged"}, format="json")
        self.assertEqual(res.status_code, 400)

    def test_invalid_transition_rejected_with_allowed(self):
        ticket = self._ticket()
        self.client.force_authenticate(self.agent)
        res = self.client.post(
            f"/manage/tickets/{ticket.id}/status",
            {"status": "action_pending", "reason": "skip"},
            format="json")
        self.assertEqual(res.status_code, 400, res.data)
        self.assertIn("allowed", res.data)
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, "open")

    def test_full_lifecycle_records_actor_reason_evidence(self):
        ticket = self._ticket()
        self.client.force_authenticate(self.agent)
        path = ["triaged", "investigating", "awaiting_party",
                "decision", "action_pending", "resolved"]
        for nxt in path:
            res = self.client.post(
                f"/manage/tickets/{ticket.id}/status",
                {"status": nxt, "reason": f"moving to {nxt}",
                 "evidence": "notes/ev-1"},
                format="json")
            self.assertEqual(res.status_code, 200,
                             f"{nxt}: {res.data}")
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, "resolved")
        events = AuditEvent.objects.filter(
            action="support.ticket_status", entity_id=str(ticket.id))
        self.assertEqual(events.count(), len(path))
        last = events.latest("created_at")
        self.assertEqual(last.after["status"], "resolved")
        self.assertEqual(last.after["evidence"], "notes/ev-1")
        self.assertEqual(last.reason, "moving to resolved")

        res = self.client.post(
            f"/manage/tickets/{ticket.id}/status",
            {"status": "reopened", "reason": "customer replied"},
            format="json")
        self.assertEqual(res.status_code, 200, res.data)

    def test_reply_path_validates_transition_and_audits(self):
        ticket = self._ticket(user=self.owner)
        self.client.force_authenticate(self.agent)
        # open → action_pending is not a legal edge, even via reply.
        res = self.client.post(
            f"/core/support/tickets/{ticket.id}/reply",
            {"status": "action_pending", "body": "trying"},
            format="json")
        self.assertEqual(res.status_code, 400)
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, "open")

        res = self.client.post(
            f"/core/support/tickets/{ticket.id}/reply",
            {"status": "triaged", "body": "Triaging this case now."},
            format="json")
        self.assertEqual(res.status_code, 201, res.data)
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, "triaged")
        self.assertTrue(AuditEvent.objects.filter(
            action="support.ticket_status",
            entity_id=str(ticket.id)).exists())

    def test_owner_cannot_transition_via_reply(self):
        ticket = self._ticket(user=self.pm)
        self.client.force_authenticate(self.pm)
        res = self.client.post(
            f"/core/support/tickets/{ticket.id}/reply",
            {"status": "resolved", "body": "closing my own"},
            format="json")
        self.assertEqual(res.status_code, 403)
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, "open")

    def test_finance_role_maker_checker(self):
        from apps.pay.models import Wallet, Withdrawal
        wallet = Wallet.objects.create(user=self.owner)
        w = Withdrawal.objects.create(
            wallet=wallet, user=self.owner,
            amount=Decimal("9000"), reference="W-fin",
            bank_name="B", bank_code="001",
            account_number="1", account_name="N")

        self.client.force_authenticate(self.fin_maker)
        res = self.client.post(
            f"/manage/withdrawals/{w.id}/mark_completed",
            {"settlement_reference": "tx-fin"}, format="json")
        self.assertEqual(res.status_code, 202, res.data)
        approval_id = res.data["approval_id"]

        # Maker cannot self-approve; another finance user can.
        res = self.client.post(f"/manage/approvals/{approval_id}/approve")
        self.assertEqual(res.status_code, 403)

        self.client.force_authenticate(self.fin_checker)
        res = self.client.post(f"/manage/approvals/{approval_id}/approve")
        self.assertEqual(res.status_code, 200, res.data)
        w.refresh_from_db()
        self.assertEqual(w.status, "completed")

    def test_gate_checklists_surfaced_on_records(self):
        designer = Designer.objects.create(
            user=User.objects.create_user(
                email="g@example.com", username="gd",
                password="password123"),
            brand_name="G", status=Designer.Status.PENDING)
        self.client.force_authenticate(self.agent)
        res = self.client.get(f"/manage/designers/{designer.id}")
        self.assertEqual(res.status_code, 200, res.data)
        gate = res.data.get("data", res.data).get("approval_gate", {})
        self.assertIn("checks", gate)
        self.assertFalse(gate["passed"])  # missing evidence

        product = Product.objects.create(
            user=designer.user, name="Gown", description="d", price=20)
        res = self.client.get(f"/manage/products/{product.id}")
        self.assertEqual(res.status_code, 200, res.data)
        gate = res.data.get("data", res.data).get("moderation_gate", {})
        self.assertIn("media", gate["checks"])

    def test_ticket_serializer_exposes_allowed_transitions(self):
        ticket = self._ticket()
        self.client.force_authenticate(self.agent)
        res = self.client.get(f"/manage/tickets/{ticket.id}")
        self.assertEqual(res.status_code, 200, res.data)
        data = res.data.get("data", res.data)
        self.assertIn("triaged", data["allowed_transitions"])
        self.assertNotIn("decision", data["allowed_transitions"])


class AdminGovernanceTests(APITestCase):
    """SUP-03 refund safety, OPS-03 order transitions, SUP-01 cases,
    SUP-04 escalation, GOV-01..04, DES-03..05, FIN-05/06, CAT-02."""

    def setUp(self):
        self.admin = User.objects.create_superuser(
            email="root@example.com", password="password123")
        self.admin2 = User.objects.create_superuser(
            email="root2@example.com", password="password123")
        self.agent = _admin("sa2@example.com", "support_agent")
        self.fin = _admin("fin3@example.com", "finance")
        self.risk = _admin("risk@example.com", "risk")
        self.user = User.objects.create_user(
            email="cust@example.com", username="cust", password="pw")
        self.designer_user = User.objects.create_user(
            email="ds@example.com", username="ds", password="pw")
        self.designer = Designer.objects.create(
            user=self.designer_user, brand_name="D",
            status=Designer.Status.APPROVED, bio="b",
            instagram="https://x.com/d", ships_internationally="yes")

    def _dispute(self, amount="100.00"):
        from apps.customers.models import Dispute, ReturnRequest
        order, _ = _paid_order(self.user, amount=amount)
        item = order.items.first()
        item.product = Product.objects.create(
            user=self.designer_user, name="G", description="d", price=10)
        item.save()
        rr = ReturnRequest.objects.create(order_item=item, reason="damaged")
        return Dispute.objects.create(
            return_request=rr, opened_by=self.user), item

    def test_refund_capped_at_collected_and_no_double_pay(self):
        dispute, item = self._dispute("100.00")
        collected = item.sub_total
        self.client.force_authenticate(self.admin)

        res = self.client.post(
            f"/manage/disputes/{dispute.dispute_id}/resolve",
            {"resolution": "partial_refund",
             "refund_amount": str(collected + 50)}, format="json")
        self.assertEqual(res.status_code, 400, res.data)
        self.assertIn("refund_context", res.data)

        res = self.client.post(
            f"/manage/disputes/{dispute.dispute_id}/resolve",
            {"resolution": "partial_refund",
             "refund_amount": str(collected)}, format="json")
        self.assertEqual(res.status_code, 200, res.data)
        dispute.refresh_from_db()
        self.assertEqual(dispute.refund_amount, collected)
        # The item's return now awaits settlement evidence.
        dispute.return_request.refresh_from_db()
        self.assertEqual(dispute.return_request.status, "refund_pending")

        # Second resolution cannot pay again.
        res = self.client.post(
            f"/manage/disputes/{dispute.dispute_id}/resolve",
            {"resolution": "refund_approved",
             "refund_amount": "10"}, format="json")
        self.assertEqual(res.status_code, 400)

    def test_refund_context_endpoint_and_unpaid_order(self):
        dispute, item = self._dispute("100.00")
        self.client.force_authenticate(self.admin)
        res = self.client.get(
            f"/manage/disputes/{dispute.dispute_id}/refund-context")
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(
            Decimal(res.data["data"]["remaining_refundable"]),
            item.sub_total)

        # Unpaid order: nothing collected → refund impossible.
        # (Payment.save() re-marks paid when status stays 'success'.)
        order = item.order
        order.invoice.payment.is_paid = False
        order.invoice.payment.status = 'pending'
        order.invoice.payment.save()
        res = self.client.post(
            f"/manage/disputes/{dispute.dispute_id}/resolve",
            {"resolution": "partial_refund", "refund_amount": "1"},
            format="json")
        self.assertEqual(res.status_code, 400)

    def test_order_transitions_enforced_with_reason(self):
        order, _ = _paid_order(self.user)
        self.client.force_authenticate(self.agent)
        url = f"/manage/orders/{order.pk}"

        # pending → delivered is not a legal edge.
        res = self.client.patch(
            url, {"status": "delivered", "reason": "jump"}, format="json")
        self.assertEqual(res.status_code, 400, res.data)

        # Legal edge still needs a reason.
        res = self.client.patch(url, {"status": "processing"},
                                format="json")
        self.assertEqual(res.status_code, 400)

        res = self.client.patch(
            url, {"status": "processing", "reason": "acknowledged"},
            format="json")
        self.assertEqual(res.status_code, 200, res.data)
        order.refresh_from_db()
        self.assertEqual(order.status, "processing")
        self.assertTrue(AuditEvent.objects.filter(
            action="orders.status_change",
            entity_id=str(order.pk)).exists())

        # Idempotent: re-asserting the same status is a no-op 200.
        res = self.client.patch(
            url, {"status": "processing"}, format="json")
        self.assertEqual(res.status_code, 200, res.data)

    def test_case_inbox_owner_and_transitions(self):
        from apps.administrator.models import Case
        ticket = SupportTicket.objects.create(
            subject="order issue", description="d", user=self.user)
        self.client.force_authenticate(self.agent)
        res = self.client.post("/manage/cases", {
            "subject": "Order issue case", "category": "ticket",
            "ticket": str(ticket.id)}, format="json")
        self.assertEqual(res.status_code, 201, res.data)
        case_id = res.data.get("id") or res.data["data"]["id"]
        case = Case.objects.get(id=case_id)
        self.assertTrue(case.case_ref.startswith("CASE-"))

        res = self.client.post(
            f"/manage/cases/{case.id}/status",
            {"status": "triaged", "reason": "classified"}, format="json")
        self.assertEqual(res.status_code, 200, res.data)
        case.refresh_from_db()
        self.assertEqual(case.status, "triaged")
        self.assertEqual(case.ticket_id, ticket.id)

        res = self.client.post(
            f"/manage/cases/{case.id}/assign",
            {"owner": str(self.agent.id)}, format="json")
        self.assertEqual(res.status_code, 200, res.data)
        case.refresh_from_db()
        self.assertEqual(case.owner_id, self.agent.id)

    def test_escalation_rules_flag_overdue_and_disputes(self):
        from apps.administrator.queues import (
            apply_escalation_rules, sync_work_queues)
        from apps.administrator.models import WorkItem
        overdue = WorkItem.objects.create(
            queue="order_pending", entity_type="Order",
            entity_id="O-1", title="late", status="open",
            priority="medium",
            due_at=timezone.now() - timedelta(hours=1))
        apply_escalation_rules()
        overdue.refresh_from_db()
        self.assertTrue(overdue.escalated)
        self.assertEqual(overdue.escalated_reason, "sla_breach")
        self.assertEqual(overdue.priority, "high")

        # Open disputes land in the support queue, escalated.
        self._dispute()
        sync_work_queues()
        item = WorkItem.objects.get(
            queue="support_case", entity_type="Dispute")
        self.assertTrue(item.escalated)

    def test_capability_grant_flow_and_sensitive_approval(self):
        from apps.administrator.capabilities import has_capability
        self.client.force_authenticate(self.admin)
        self.assertFalse(has_capability(self.agent, 'catalog.publish'))

        # Non-sensitive grant applies immediately.
        res = self.client.post("/manage/capability-grants", {
            "user": str(self.agent.id),
            "capability": "catalog.publish", "granted": True,
            "reason": "covering catalog shift"}, format="json")
        self.assertEqual(res.status_code, 201, res.data)
        self.assertTrue(has_capability(self.agent, 'catalog.publish'))

        # Sensitive grant → maker-checker; a second approver activates it.
        res = self.client.post("/manage/capability-grants", {
            "user": str(self.agent.id),
            "capability": "finance.refund", "granted": True,
            "reason": "refund coverage"}, format="json")
        self.assertEqual(res.status_code, 202, res.data)
        self.assertFalse(has_capability(self.agent, 'finance.refund'))
        approval_id = res.data["approval_id"]

        self.client.force_authenticate(self.admin)
        self.assertEqual(self.client.post(
            f"/manage/approvals/{approval_id}/approve").status_code, 403)
        self.client.force_authenticate(self.admin2)
        res = self.client.post(
            f"/manage/approvals/{approval_id}/approve")
        self.assertEqual(res.status_code, 200, res.data)
        self.assertTrue(has_capability(self.agent, 'finance.refund'))

        # Revoke takes effect immediately.
        grant = CapabilityGrant.objects.get(capability='finance.refund')
        res = self.client.post(
            f"/manage/capability-grants/{grant.id}/revoke")
        self.assertEqual(res.status_code, 200)
        self.assertFalse(has_capability(self.agent, 'finance.refund'))

    def test_policy_registry_publish_rollback(self):
        from apps.administrator.models import PolicyVersion
        self.client.force_authenticate(self.admin)
        res = self.client.post("/manage/policies", {
            "key": "return_window_days", "value": {"days": 14},
            "impact": "low", "reason": "extend window"}, format="json")
        self.assertEqual(res.status_code, 201, res.data)
        pid = res.data.get("id") or res.data["data"]["id"]

        res = self.client.post(f"/manage/policies/{pid}/publish")
        self.assertEqual(res.status_code, 200, res.data)

        # High-impact: author cannot self-publish.
        res = self.client.post("/manage/policies", {
            "key": "commission_rate", "value": {"rate": 0.12},
            "impact": "high", "reason": "rate change"}, format="json")
        pid2 = res.data.get("id") or res.data["data"]["id"]
        res = self.client.post(f"/manage/policies/{pid2}/publish")
        self.assertEqual(res.status_code, 403)
        self.client.force_authenticate(self.admin2)
        res = self.client.post(f"/manage/policies/{pid2}/publish")
        self.assertEqual(res.status_code, 200, res.data)

        # Rollback creates a new version with the old value.
        self.client.force_authenticate(self.admin)
        res = self.client.post(f"/manage/policies/{pid}/rollback",
                               {"reason": "revert"}, format="json")
        self.assertEqual(res.status_code, 200, res.data)
        rolled = PolicyVersion.objects.get(
            key="return_window_days", status="published")
        self.assertEqual(rolled.version, 2)

    def test_privacy_request_deadline_and_deletion_rules(self):
        self.client.force_authenticate(self.risk)
        res = self.client.post("/manage/privacy-requests", {
            "request_type": "deletion",
            "subject_email": "cust@example.com",
            "subject_user": str(self.user.id)}, format="json")
        self.assertEqual(res.status_code, 201, res.data)
        pid = res.data.get("id") or res.data["data"]["id"]

        res = self.client.post(f"/manage/privacy-requests/{pid}/verify")
        self.assertEqual(res.status_code, 200)

        # Deletion cannot complete without propagation or an exception.
        res = self.client.post(f"/manage/privacy-requests/{pid}/complete")
        self.assertEqual(res.status_code, 400)
        res = self.client.post(f"/manage/privacy-requests/{pid}/complete",
                               {"retention_exception":
                                "financial records retained 7y"},
                               format="json")
        self.assertEqual(res.status_code, 200, res.data)

        # Marketer cannot see privacy requests at all.
        marketer = _admin("mk2@example.com", "marketer")
        self.client.force_authenticate(marketer)
        self.assertEqual(
            self.client.get("/manage/privacy-requests").status_code, 403)

    def test_incident_queue_and_transitions(self):
        self.client.force_authenticate(self.risk)
        res = self.client.post("/manage/incidents", {
            "title": "Payment webhook outage", "severity": "sev1",
            "summary": "payments not confirming"}, format="json")
        self.assertEqual(res.status_code, 201, res.data)
        iid = res.data.get("id") or res.data["data"]["id"]

        from apps.administrator.queues import sync_work_queues
        from apps.administrator.models import WorkItem
        sync_work_queues()
        item = WorkItem.objects.get(queue="incident", entity_id=str(iid))
        self.assertTrue(item.escalated)
        self.assertEqual(item.priority, "urgent")

        res = self.client.post(f"/manage/incidents/{iid}/status",
                               {"status": "mitigating",
                                "reason": "failover running"},
                               format="json")
        self.assertEqual(res.status_code, 200, res.data)
        res = self.client.post(f"/manage/incidents/{iid}/timeline",
                               {"note": "provider notified"}, format="json")
        self.assertEqual(res.status_code, 200, res.data)

    def test_designer_activation_health_suspend(self):
        designer = self.designer
        self.client.force_authenticate(self.admin)

        res = self.client.get(f"/manage/designers/{designer.id}/activation")
        self.assertEqual(res.status_code, 200, res.data)
        steps = res.data["data"]["steps"]
        self.assertTrue(steps["approved"]["done"])
        self.assertFalse(steps["first_paid_order"]["done"])
        self.assertEqual(res.data["data"]["stalled_at"],
                         "first_published_product")

        res = self.client.get(f"/manage/designers/{designer.id}/health")
        self.assertEqual(res.status_code, 200, res.data)
        self.assertIn("components", res.data["data"])
        self.assertIn("fulfillment_reliability",
                      res.data["data"]["components"])

        # Suspension requires reason + risk category + review date.
        res = self.client.post(f"/manage/designers/{designer.id}/suspend",
                               {"reason": "x"}, format="json")
        self.assertEqual(res.status_code, 400)
        res = self.client.post(
            f"/manage/designers/{designer.id}/suspend",
            {"reason": "repeat defects", "risk_category": "quality",
             "review_date": "2026-11-01", "notice": "email sent"},
            format="json")
        self.assertEqual(res.status_code, 200, res.data)
        designer.refresh_from_db()
        self.assertEqual(designer.status, "blocked")
        self.assertEqual(designer.suspension["risk_category"], "quality")

        res = self.client.post(
            f"/manage/designers/{designer.id}/reinstate",
            {"reason": "review passed"}, format="json")
        self.assertEqual(res.status_code, 200, res.data)
        designer.refresh_from_db()
        self.assertEqual(designer.status, "approved")

    def test_finance_summary_and_forecast(self):
        _paid_order(self.user, amount="100.00")
        self.client.force_authenticate(self.fin)
        res = self.client.get("/manage/finance/summary")
        self.assertEqual(res.status_code, 200, res.data)
        self.assertIn("gmv", res.data["data"])
        self.assertIn("definitions", res.data["data"]["meta"])

        res = self.client.get("/manage/finance/liability-forecast")
        self.assertEqual(res.status_code, 200, res.data)
        self.assertIn("liabilities", res.data["data"])

        # Read-only for finance role; marketer locked out.
        marketer = _admin("mk3@example.com", "marketer")
        self.client.force_authenticate(marketer)
        self.assertEqual(
            self.client.get("/manage/finance/summary").status_code, 403)

    def test_inventory_health_flags(self):
        Product.objects.create(user=self.designer_user, name="NoStock",
                               description="d", price=10, stock=0,
                               is_published=True, is_active=True)
        self.client.force_authenticate(self.admin)
        res = self.client.get("/manage/inventory/health")
        self.assertEqual(res.status_code, 200, res.data)
        flags = [r["name"] for r in res.data["data"]["out_of_stock"]]
        self.assertIn("NoStock", flags)
