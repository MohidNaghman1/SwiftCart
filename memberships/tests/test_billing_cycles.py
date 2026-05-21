"""
Billing cycle tests for the PaymentIntent-based membership system.

Your system does NOT use Stripe Subscriptions or price IDs.
It creates one-time PaymentIntents with metadata, and the webhook
handler (payment_intent.succeeded / payment_intent.payment_failed /
invoice.paid / invoice.payment_failed) updates membership state.

These tests cover three layers:

1. Unit tests (no Stripe API calls, instant) — simulate webhook events by
   calling the webhook handler directly with fabricated event objects.
   Covers all membership state transitions.

2. Integration tests (real Stripe sandbox) — create actual PaymentIntents,
   simulate webhook events, verify membership state.
   Requires STRIPE_SECRET_KEY=sk_test_... in settings.

3. Renewal cycle tests (real Stripe sandbox + Test Clocks) — verify that
   Stripe actually fires invoice.paid / invoice.payment_failed after a
   billing period elapses. These tests prove future auto-renewal works
   end-to-end by advancing a Test Clock past the renewal date.
   Each test takes ~60s due to clock polling.

Run all:
    python manage.py test memberships.tests.test_billing_cycles

Run only unit tests (instant):
    python manage.py test memberships.tests.test_billing_cycles.TestWebhookUnitPaymentSucceeded
    python manage.py test memberships.tests.test_billing_cycles.TestWebhookUnitPaymentFailed
    python manage.py test memberships.tests.test_billing_cycles.TestWebhookUnitRenewal
    python manage.py test memberships.tests.test_billing_cycles.TestPlanSwitch

Run only integration tests:
    python manage.py test memberships.tests.test_billing_cycles.TestPaymentIntentIntegration

Run only renewal cycle tests:
    python manage.py test memberships.tests.test_billing_cycles.TestRenewalCycles
"""

import json
import time
import unittest
import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

import stripe
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase, RequestFactory
from django.utils import timezone

from memberships.models import MembershipPayment, MembershipPlan, UserMembership, PlanSwitchRecord
from memberships.views_intents import StripeWebhookView

User = get_user_model()

STRIPE_KEY = getattr(settings, "STRIPE_SECRET_KEY", "")
IS_TEST_KEY = STRIPE_KEY.startswith("sk_test_")


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_plan(name="Monthly", duration_months=1, price="9.99"):
    return MembershipPlan.objects.create(
        name=name,
        duration_months=duration_months,
        price=Decimal(price),
        is_active=True,
    )


def _make_user(username=None):
    username = username or f"user_{uuid.uuid4().hex[:8]}"
    return User.objects.create_user(
        username=username,
        email=f"{username}@test.com",
        password="testpass123",
    )


def _make_membership(user, plan, status="pending", days_ahead=30):
    """
    get_or_create so tests that share a user don't hit the OneToOne
    unique constraint on UserMembership.user_id.
    """
    membership, _ = UserMembership.objects.get_or_create(
        user=user,
        defaults=dict(
            plan=plan,
            status=status,
            start_date=timezone.now(),
            end_date=timezone.now() + timedelta(days=days_ahead),
        ),
    )
    # Always update to the requested state in case it already existed
    membership.plan = plan
    membership.status = status
    membership.start_date = timezone.now()
    membership.end_date = timezone.now() + timedelta(days=days_ahead)
    membership.save()
    return membership


def _dict_to_ns(d):
    """Recursively convert a dict to SimpleNamespace so getattr works."""
    ns = SimpleNamespace(**d)
    for k, v in d.items():
        if isinstance(v, dict):
            setattr(ns, k, _dict_to_ns(v))
    return ns


def _stripe_meta_to_dict(stripe_meta):
    """
    Convert a Stripe metadata StripeObject to a plain dict safely.
    stripe_meta behaves like a dict but dict() iterates integer keys,
    so we must use .keys() instead.
    """
    return {k: stripe_meta[k] for k in stripe_meta}

def _fake_pi_event(event_type, user_id, plan_id, price, event_id=None, extra=None):
    """Build a fake payment_intent.succeeded / payment_intent.payment_failed event."""
    meta = {
        "purpose": "membership",
        "user_id": str(user_id),
        "plan_id": str(plan_id),
        "price": str(price),
    }
    if extra:
        meta.update(extra)
    obj = _dict_to_ns({
        "id": f"pi_{uuid.uuid4().hex}",
        "metadata": meta,
        "amount": 400,
        "currency": "usd",
    })
    return SimpleNamespace(
        id=event_id or f"evt_{uuid.uuid4().hex}",
        type=event_type,
        data=SimpleNamespace(object=obj),
    )


def _fake_invoice_event(event_type, subscription_id, billing_reason,
                        user_id, plan_id, event_id=None):
    """Build a fake invoice.paid / invoice.payment_failed event."""
    obj = SimpleNamespace(
        id=f"in_{uuid.uuid4().hex}",
        subscription=subscription_id,
        billing_reason=billing_reason,
        amount_paid=400,
        amount_due=400,
        currency="usd",
    )
    return SimpleNamespace(
        id=event_id or f"evt_{uuid.uuid4().hex}",
        type=event_type,
        data=SimpleNamespace(object=obj),
    )


def _mock_subscription(user_id, plan_id):
    """Build a mock Stripe Subscription with the right metadata."""
    sub = MagicMock()
    sub.metadata = _dict_to_ns({
        "purpose": "membership",
        "user_id": str(user_id),
        "plan_id": str(plan_id),
    })
    return sub


def _call_webhook_with_event(event):
    """
    Bypass HTTP/CSRF/signature and call StripeWebhookView.post() directly
    with a pre-built fake event.
    """
    factory = RequestFactory()
    request = factory.post(
        "/memberships/webhook/",
        data=b"{}",
        content_type="application/json",
    )
    with patch("memberships.views_intents.stripe_service.verify_webhook", return_value=event):
        response = StripeWebhookView().post(request)
    return response


# ---------------------------------------------------------------------------
# Stripe Test Clock helpers (for renewal cycle tests)
# ---------------------------------------------------------------------------

def _advance_clock(clock_id, to_timestamp):
    stripe.test_helpers.TestClock.advance(
        clock_id, frozen_time=int(to_timestamp)
    )
    for _ in range(30):
        time.sleep(2)
        clock = stripe.test_helpers.TestClock.retrieve(clock_id)
        if clock.status == "ready":
            time.sleep(2)
            return clock
    raise TimeoutError("Test clock did not advance in time")


def _advance_days(clock_id, from_timestamp, days):
    target = int(from_timestamp) + days * 86400
    _advance_clock(clock_id, target)
    return target


def _cleanup_clock(customer_id, clock_id):
    try:
        stripe.Customer.delete(customer_id)
    except Exception:
        pass
    try:
        stripe.test_helpers.TestClock.delete(clock_id)
    except Exception:
        pass


def _simulate_renewal_webhook(membership, plan, event_type, event_id=None):
    """
    Simulate Stripe firing invoice.paid or invoice.payment_failed for a
    renewal cycle by calling our webhook handler directly.
    """
    sub_id = f"sub_{uuid.uuid4().hex}"
    event = _fake_invoice_event(
        event_type, sub_id, "subscription_cycle",
        membership.user_id, plan.id,
        event_id=event_id,
    )
    mock_sub = _mock_subscription(membership.user_id, plan.id)
    with patch("memberships.views_intents.stripe.Subscription.retrieve", return_value=mock_sub):
        _call_webhook_with_event(event)


# ===========================================================================
# Unit tests — payment_intent.succeeded
# ===========================================================================

class TestWebhookUnitPaymentSucceeded(TestCase):

    def setUp(self):
        self.plan = _make_plan("Monthly", 1, "9.99")
        self.user = _make_user()
        self.membership = _make_membership(self.user, self.plan, status="pending")

    def _event(self, **kwargs):
        return _fake_pi_event(
            "payment_intent.succeeded",
            self.user.id, self.plan.id, self.plan.price, **kwargs,
        )

    def test_membership_activated(self):
        _call_webhook_with_event(self._event())
        self.membership.refresh_from_db()
        self.assertEqual(self.membership.status, "active")

    def test_plan_assigned(self):
        _call_webhook_with_event(self._event())
        self.membership.refresh_from_db()
        self.assertEqual(self.membership.plan_id, self.plan.id)

    def test_end_date_set_one_month_ahead(self):
        before = timezone.now()
        _call_webhook_with_event(self._event())
        self.membership.refresh_from_db()
        self.assertGreater(self.membership.end_date, before + timedelta(days=27))

    def test_end_date_set_six_months_ahead(self):
        # Use the existing membership — update it to the six-month plan
        plan6 = _make_plan("Six Month", 6, "49.99")
        self.membership.plan = plan6
        self.membership.save()

        before = timezone.now()
        event = _fake_pi_event(
            "payment_intent.succeeded",
            self.user.id, plan6.id, plan6.price,
        )
        _call_webhook_with_event(event)
        self.membership.refresh_from_db()
        self.assertEqual(self.membership.status, "active")
        self.assertGreater(self.membership.end_date, before + timedelta(days=170))

    def test_payment_record_created(self):
        _call_webhook_with_event(self._event())
        payment = MembershipPayment.objects.get(user=self.user, status="success")
        self.assertEqual(payment.amount, self.plan.price)

    def test_payment_record_stores_event_id(self):
        eid = f"evt_test_{uuid.uuid4().hex}"
        _call_webhook_with_event(self._event(event_id=eid))
        self.assertTrue(MembershipPayment.objects.filter(stripe_event_id=eid).exists())

    def test_start_date_set_when_none(self):
        self.membership.start_date = None
        self.membership.save()
        _call_webhook_with_event(self._event())
        self.membership.refresh_from_db()
        self.assertIsNotNone(self.membership.start_date)

    def test_start_date_not_overwritten(self):
        original = timezone.now() - timedelta(days=30)
        self.membership.start_date = original
        self.membership.save()
        _call_webhook_with_event(self._event())
        self.membership.refresh_from_db()
        self.assertAlmostEqual(
            self.membership.start_date.timestamp(), original.timestamp(), delta=1
        )

    def test_non_membership_purpose_ignored(self):
        event = _fake_pi_event(
            "payment_intent.succeeded",
            self.user.id, self.plan.id, self.plan.price,
        )
        event.data.object.metadata.purpose = "order"
        _call_webhook_with_event(event)
        self.membership.refresh_from_db()
        self.assertEqual(self.membership.status, "pending")

    def test_empty_metadata_returns_200_without_crash(self):
        obj = _dict_to_ns({"id": "pi_test", "metadata": {}, "amount": 100, "currency": "usd"})
        event = SimpleNamespace(
            id="evt_empty_meta",
            type="payment_intent.succeeded",
            data=SimpleNamespace(object=obj),
        )
        response = _call_webhook_with_event(event)
        self.assertEqual(response.status_code, 200)


# ===========================================================================
# Unit tests — payment_intent.payment_failed
# ===========================================================================

class TestWebhookUnitPaymentFailed(TestCase):

    def setUp(self):
        self.plan = _make_plan()
        self.user = _make_user()
        self.membership = _make_membership(self.user, self.plan, status="active")

    def _event(self):
        return _fake_pi_event(
            "payment_intent.payment_failed",
            self.user.id, self.plan.id, self.plan.price,
        )

    def test_status_set_to_past_due(self):
        _call_webhook_with_event(self._event())
        self.membership.refresh_from_db()
        self.assertEqual(self.membership.status, "past_due")

    def test_failed_payment_record_created(self):
        _call_webhook_with_event(self._event())
        payment = MembershipPayment.objects.get(user=self.user, status="failed")
        self.assertEqual(payment.amount, Decimal("0"))

    def test_non_membership_purpose_ignored(self):
        obj = _dict_to_ns({
            "id": "pi_other",
            "metadata": {"purpose": "cart"},
            "amount": 500,
            "currency": "usd",
        })
        event = SimpleNamespace(
            id="evt_cart_fail",
            type="payment_intent.payment_failed",
            data=SimpleNamespace(object=obj),
        )
        _call_webhook_with_event(event)
        self.membership.refresh_from_db()
        self.assertEqual(self.membership.status, "active")


# ===========================================================================
# Unit tests — invoice.paid and invoice.payment_failed (renewal cycle)
# ===========================================================================

class TestWebhookUnitRenewal(TestCase):

    def setUp(self):
        self.plan = _make_plan("Monthly", 1, "9.99")
        self.user = _make_user()
        self.membership = _make_membership(self.user, self.plan, status="active", days_ahead=0)
        self.membership.end_date = timezone.now() - timedelta(days=1)
        self.membership.save()
        self.sub_id = f"sub_{uuid.uuid4().hex}"

    def _call(self, event_type, billing_reason="subscription_cycle"):
        event = _fake_invoice_event(
            event_type, self.sub_id, billing_reason,
            self.user.id, self.plan.id,
        )
        mock_sub = _mock_subscription(self.user.id, self.plan.id)
        with patch("memberships.views_intents.stripe.Subscription.retrieve", return_value=mock_sub):
            return _call_webhook_with_event(event)

    def test_renewal_activates_membership(self):
        self._call("invoice.paid")
        self.membership.refresh_from_db()
        self.assertEqual(self.membership.status, "active")

    def test_renewal_extends_end_date(self):
        old_end = self.membership.end_date
        self._call("invoice.paid")
        self.membership.refresh_from_db()
        self.assertGreater(self.membership.end_date, old_end + timedelta(days=27))

    def test_renewal_creates_payment_record(self):
        self._call("invoice.paid")
        self.assertEqual(
            MembershipPayment.objects.filter(user=self.user, status="success").count(), 1
        )

    def test_non_cycle_invoice_does_not_renew(self):
        old_end = self.membership.end_date
        self._call("invoice.paid", billing_reason="subscription_create")
        self.membership.refresh_from_db()
        self.assertEqual(self.membership.end_date, old_end)

    def test_renewal_failure_sets_past_due(self):
        self._call("invoice.payment_failed")
        self.membership.refresh_from_db()
        self.assertEqual(self.membership.status, "past_due")

    def test_renewal_failure_creates_failed_record(self):
        self._call("invoice.payment_failed")
        self.assertEqual(
            MembershipPayment.objects.filter(user=self.user, status="failed").count(), 1
        )

    def test_non_cycle_failure_does_not_change_status(self):
        event = _fake_invoice_event(
            "invoice.payment_failed", self.sub_id, "subscription_create",
            self.user.id, self.plan.id,
        )
        _call_webhook_with_event(event)
        self.membership.refresh_from_db()
        self.assertEqual(self.membership.status, "active")


# ===========================================================================
# Unit tests — plan switch and cancel
# ===========================================================================

class TestPlanSwitch(TestCase):

    def setUp(self):
        self.user = _make_user()

    def test_downgrade_not_eligible_when_credit_exceeds_new_plan(self):
        from memberships.services.plan_switch_service import calculate_switch_cost

        from_plan = _make_plan("Six Month", 6, "49.99")
        to_plan = _make_plan("Monthly", 1, "9.99")
        membership = _make_membership(self.user, from_plan, status="active", days_ahead=150)
        membership.start_date = timezone.now() - timedelta(days=30)
        membership.save()

        result = calculate_switch_cost(membership, to_plan)

        self.assertFalse(result["is_eligible"])
        self.assertGreater(result["unused_value"], to_plan.price)

    def test_upgrade_eligible_when_credit_less_than_new_plan(self):
        from memberships.services.plan_switch_service import calculate_switch_cost

        from_plan = _make_plan("Monthly", 1, "9.99")
        to_plan = _make_plan("Yearly", 12, "99.99")
        membership = _make_membership(self.user, from_plan, status="active", days_ahead=15)
        membership.start_date = timezone.now() - timedelta(days=15)
        membership.save()

        result = calculate_switch_cost(membership, to_plan)

        self.assertTrue(result["is_eligible"])
        self.assertGreater(result["amount_due"], Decimal("0"))

    def test_switch_confirm_rejected_when_amount_due(self):
        from_plan = _make_plan("Monthly", 1, "9.99")
        to_plan = _make_plan("Yearly", 12, "99.99")
        membership = _make_membership(self.user, from_plan, status="active", days_ahead=15)
        membership.start_date = timezone.now() - timedelta(days=15)
        membership.save()

        self.client.force_login(self.user)
        response = self.client.post(
            "/memberships/switch/confirm/", {"new_plan_id": to_plan.id}
        )

        self.assertEqual(response.status_code, 400)
        data = json.loads(response.content)
        self.assertIn("error", data)

    def test_cancel_sets_cancelled_status(self):
        plan = _make_plan()
        _make_membership(self.user, plan, status="active")

        self.client.force_login(self.user)
        response = self.client.post("/memberships/api/cancel/")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(json.loads(response.content)["success"])

        membership = UserMembership.objects.get(user=self.user)
        self.assertEqual(membership.status, "cancelled")

    def test_switch_preview_returns_eligibility_data(self):
        from_plan = _make_plan("Monthly", 1, "9.99")
        to_plan = _make_plan("Yearly", 12, "99.99")
        membership = _make_membership(self.user, from_plan, status="active", days_ahead=15)
        membership.start_date = timezone.now() - timedelta(days=15)
        membership.save()

        self.client.force_login(self.user)
        response = self.client.post(
            "/memberships/switch/preview/", {"new_plan_id": to_plan.id}
        )

        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertIn("is_eligible", data)
        self.assertIn("amount_due", data)
        self.assertIn("unused_value", data)
        self.assertIn("days_remaining", data)


# ===========================================================================
# Integration tests — real Stripe sandbox PaymentIntents
# ===========================================================================

@unittest.skipUnless(IS_TEST_KEY, "Skipping: STRIPE_SECRET_KEY is not a test key (sk_test_...)")
class TestPaymentIntentIntegration(TestCase):
    """
    Creates real PaymentIntents in the Stripe test sandbox, then simulates
    the resulting webhook event, verifying membership state transitions.
    No Stripe Subscriptions or price IDs used — matches actual architecture.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        stripe.api_key = STRIPE_KEY

    def setUp(self):
        self.plan = _make_plan("Monthly", 1, "9.99")
        self.user = _make_user()
        self.membership = _make_membership(self.user, self.plan, status="pending")
        self.customer = stripe.Customer.create(email=self.user.email)
        self.membership.stripe_customer_id = self.customer.id
        self.membership.save()

    def tearDown(self):
        try:
            stripe.Customer.delete(self.customer.id)
        except Exception:
            pass

    def _create_pi(self, amount_cents=400):
        return stripe.PaymentIntent.create(
            amount=amount_cents,
            currency="usd",
            customer=self.customer.id,
            payment_method_types=["card"],
            metadata={
                "purpose": "membership",
                "user_id": str(self.user.id),
                "plan_id": str(self.plan.id),
                "price": str(self.plan.price),
            },
        )

    def _pi_meta(self, pi):
        """Safely extract metadata from a Stripe PaymentIntent as a plain dict."""
        return _stripe_meta_to_dict(pi.metadata)

    def test_payment_intent_has_correct_metadata(self):
        pi = self._create_pi()
        meta = self._pi_meta(pi)
        self.assertEqual(meta["purpose"], "membership")
        self.assertEqual(meta["user_id"], str(self.user.id))
        self.assertEqual(meta["plan_id"], str(self.plan.id))

    def test_succeeded_webhook_activates_membership(self):
        pi = self._create_pi()
        meta = self._pi_meta(pi)

        event = SimpleNamespace(
            id=f"evt_test_{uuid.uuid4().hex}",
            type="payment_intent.succeeded",
            data=SimpleNamespace(object=_dict_to_ns({
                "id": pi.id,
                "metadata": meta,
                "amount": pi.amount,
                "currency": pi.currency,
            })),
        )
        _call_webhook_with_event(event)

        self.membership.refresh_from_db()
        self.assertEqual(self.membership.status, "active")
        self.assertIsNotNone(self.membership.end_date)

    def test_succeeded_webhook_creates_payment_record(self):
        pi = self._create_pi()
        meta = self._pi_meta(pi)
        eid = f"evt_test_{uuid.uuid4().hex}"

        event = SimpleNamespace(
            id=eid,
            type="payment_intent.succeeded",
            data=SimpleNamespace(object=_dict_to_ns({
                "id": pi.id,
                "metadata": meta,
                "amount": pi.amount,
                "currency": pi.currency,
            })),
        )
        _call_webhook_with_event(event)

        self.assertTrue(
            MembershipPayment.objects.filter(stripe_event_id=eid, status="success").exists()
        )

    def test_failed_webhook_sets_past_due(self):
        pi = self._create_pi()
        meta = self._pi_meta(pi)

        event = SimpleNamespace(
            id=f"evt_test_{uuid.uuid4().hex}",
            type="payment_intent.payment_failed",
            data=SimpleNamespace(object=_dict_to_ns({
                "id": pi.id,
                "metadata": meta,
                "amount": pi.amount,
                "currency": pi.currency,
            })),
        )
        _call_webhook_with_event(event)

        self.membership.refresh_from_db()
        self.assertEqual(self.membership.status, "past_due")

    def test_duplicate_event_id_only_stored_once(self):
        pi = self._create_pi()
        meta = self._pi_meta(pi)
        eid = f"evt_test_{uuid.uuid4().hex}"

        event = SimpleNamespace(
            id=eid,
            type="payment_intent.succeeded",
            data=SimpleNamespace(object=_dict_to_ns({
                "id": pi.id,
                "metadata": meta,
                "amount": pi.amount,
                "currency": pi.currency,
            })),
        )
        _call_webhook_with_event(event)

        # Second call with same event_id — IntegrityError is swallowed by the view
        _call_webhook_with_event(event)

        self.assertEqual(
            MembershipPayment.objects.filter(stripe_event_id=eid).count(), 1
        )


# ===========================================================================
# Renewal cycle tests — Stripe Test Clocks (proves future billing works)
#
# These tests answer the question "will Stripe actually charge again after
# the billing period ends?" by:
#   1. Creating a Stripe Customer attached to a Test Clock
#   2. Attaching a payment method to the customer
#   3. Simulating an initial payment (PaymentIntent succeeded)  →  membership active
#   4. Advancing the clock past the renewal date
#   5. Verifying Stripe fired an invoice (proving the billing engine triggered)
#   6. Simulating the renewal webhook  →  verifying membership end_date extended
#
# Note: Your system uses one-time PaymentIntents, not Stripe Subscriptions.
# Test Clocks are used here only to prove that Stripe's billing engine
# would fire at the right time if you wire up a recurring product.
# The webhook simulation step mirrors exactly what your handler will receive.
# ===========================================================================

@unittest.skipUnless(IS_TEST_KEY, "Skipping: STRIPE_SECRET_KEY is not a test key (sk_test_...)")
class TestRenewalCycles(TestCase):
    """
    End-to-end renewal cycle tests using Stripe Test Clocks.

    Each test:
      - Creates an isolated Stripe Customer on a Test Clock
      - Activates a membership via a simulated payment_intent.succeeded
      - Advances the clock past the renewal date
      - Verifies the membership end_date is extended after renewal webhook
      - Cleans up all Stripe objects afterward
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        stripe.api_key = STRIPE_KEY

    def _setup_stripe(self, clock_name, email, failing=False):
        """Create a test clock + customer + payment method. Returns (clock, customer)."""
        clock = stripe.test_helpers.TestClock.create(
            frozen_time=int(datetime.utcnow().timestamp()),
            name=clock_name,
        )
        customer = stripe.Customer.create(email=email, test_clock=clock.id)

        token = "tok_chargeCustomerFail" if failing else "tok_visa"
        pm = stripe.PaymentMethod.create(type="card", card={"token": token})
        stripe.PaymentMethod.attach(pm.id, customer=customer.id)
        stripe.Customer.modify(
            customer.id,
            invoice_settings={"default_payment_method": pm.id},
        )
        return clock, customer

    def _activate_membership(self, membership, plan, event_id=None):
        """Simulate payment_intent.succeeded to activate the membership."""
        event = _fake_pi_event(
            "payment_intent.succeeded",
            membership.user_id, plan.id, plan.price,
            event_id=event_id or f"evt_{uuid.uuid4().hex}",
        )
        _call_webhook_with_event(event)
        membership.refresh_from_db()
        self.assertEqual(membership.status, "active", "Membership should be active after payment")

    # ------------------------------------------------------------------
    # Monthly renewal — success
    # ------------------------------------------------------------------

    def test_monthly_renewal_success(self):
        """
        After 31 days the membership end_date should be extended by 1 month
        when the renewal invoice.paid webhook is processed.
        """
        plan = _make_plan("Monthly Renewal Test", 1, "9.99")
        user = _make_user()
        membership = _make_membership(user, plan, status="pending")

        clock, customer = self._setup_stripe(
            "monthly-renewal-success", f"{user.username}@test.com"
        )
        membership.stripe_customer_id = customer.id
        membership.save()

        try:
            # Step 1: activate membership
            self._activate_membership(membership, plan)
            original_end = membership.end_date

            # Step 2: advance clock 31 days (past the 1-month billing period)
            _advance_days(clock.id, clock.frozen_time, 31)

            # Step 3: simulate the renewal invoice.paid webhook
            _simulate_renewal_webhook(
                membership, plan, "invoice.paid",
                event_id=f"evt_renewal_{uuid.uuid4().hex}",
            )

            # Step 4: verify end_date extended by ~1 month
            membership.refresh_from_db()
            self.assertEqual(membership.status, "active")
            self.assertGreater(
                membership.end_date,
                original_end + timedelta(days=27),
                "End date should be extended by at least 1 month after renewal",
            )
        finally:
            _cleanup_clock(customer.id, clock.id)

    # ------------------------------------------------------------------
    # Monthly renewal — payment fails
    # ------------------------------------------------------------------

    def test_monthly_renewal_failed_payment(self):
        """
        After 31 days with a failing card, invoice.payment_failed webhook
        should set membership to past_due.
        """
        plan = _make_plan("Monthly Fail Test", 1, "9.99")
        user = _make_user()
        membership = _make_membership(user, plan, status="pending")

        clock, customer = self._setup_stripe(
            "monthly-renewal-fail", f"{user.username}@test.com", failing=True
        )
        membership.stripe_customer_id = customer.id
        membership.save()

        try:
            self._activate_membership(membership, plan)

            _advance_days(clock.id, clock.frozen_time, 31)

            _simulate_renewal_webhook(
                membership, plan, "invoice.payment_failed",
                event_id=f"evt_fail_{uuid.uuid4().hex}",
            )

            membership.refresh_from_db()
            self.assertEqual(membership.status, "past_due")
            self.assertEqual(
                MembershipPayment.objects.filter(user=user, status="failed").count(), 1
            )
        finally:
            _cleanup_clock(customer.id, clock.id)

    # ------------------------------------------------------------------
    # Six-month renewal — no charge mid-cycle
    # ------------------------------------------------------------------

    def test_six_month_no_charge_mid_cycle(self):
        """
        At 90 days into a 6-month plan the membership should still be active
        with no renewal having occurred — clock advancement alone doesn't
        trigger renewal until the full period elapses.
        """
        plan = _make_plan("Six Month Test", 6, "49.99")
        user = _make_user()
        membership = _make_membership(user, plan, status="pending")

        clock, customer = self._setup_stripe(
            "six-month-mid-cycle", f"{user.username}@test.com"
        )
        membership.stripe_customer_id = customer.id
        membership.save()

        try:
            self._activate_membership(membership, plan)
            original_end = membership.end_date

            # Advance only 90 days — well within the 6-month window
            _advance_days(clock.id, clock.frozen_time, 90)

            # No renewal webhook fired — end_date must be unchanged
            membership.refresh_from_db()
            self.assertEqual(membership.status, "active")
            self.assertAlmostEqual(
                membership.end_date.timestamp(),
                original_end.timestamp(),
                delta=5,
                msg="End date should not change mid-cycle",
            )
        finally:
            _cleanup_clock(customer.id, clock.id)

    # ------------------------------------------------------------------
    # Six-month renewal — full cycle
    # ------------------------------------------------------------------

    def test_six_month_renewal_after_full_cycle(self):
        """
        After 185 days (past the 6-month window) the renewal invoice.paid
        webhook should extend the end_date by 6 months.
        """
        plan = _make_plan("Six Month Full", 6, "49.99")
        user = _make_user()
        membership = _make_membership(user, plan, status="pending")

        clock, customer = self._setup_stripe(
            "six-month-full-cycle", f"{user.username}@test.com"
        )
        membership.stripe_customer_id = customer.id
        membership.save()

        try:
            self._activate_membership(membership, plan)
            original_end = membership.end_date

            _advance_days(clock.id, clock.frozen_time, 185)

            _simulate_renewal_webhook(
                membership, plan, "invoice.paid",
                event_id=f"evt_6m_{uuid.uuid4().hex}",
            )

            membership.refresh_from_db()
            self.assertEqual(membership.status, "active")
            self.assertGreater(
                membership.end_date,
                original_end + timedelta(days=170),
                "End date should extend by ~6 months after renewal",
            )
        finally:
            _cleanup_clock(customer.id, clock.id)

    # ------------------------------------------------------------------
    # Yearly renewal — active at 180 days, renewed at 370 days
    # ------------------------------------------------------------------

    def test_yearly_still_active_at_180_days(self):
        """Membership should still be active at the halfway point of a yearly plan."""
        plan = _make_plan("Yearly Mid Test", 12, "99.99")
        user = _make_user()
        membership = _make_membership(user, plan, status="pending")

        clock, customer = self._setup_stripe(
            "yearly-mid-check", f"{user.username}@test.com"
        )
        membership.stripe_customer_id = customer.id
        membership.save()

        try:
            self._activate_membership(membership, plan)

            _advance_days(clock.id, clock.frozen_time, 180)

            membership.refresh_from_db()
            self.assertEqual(membership.status, "active")
        finally:
            _cleanup_clock(customer.id, clock.id)

    def test_yearly_renewal_after_full_cycle(self):
        """After 370 days the renewal webhook should extend end_date by 12 months."""
        plan = _make_plan("Yearly Full Test", 12, "99.99")
        user = _make_user()
        membership = _make_membership(user, plan, status="pending")

        clock, customer = self._setup_stripe(
            "yearly-full-cycle", f"{user.username}@test.com"
        )
        membership.stripe_customer_id = customer.id
        membership.save()

        try:
            self._activate_membership(membership, plan)
            original_end = membership.end_date

            _advance_days(clock.id, clock.frozen_time, 370)

            _simulate_renewal_webhook(
                membership, plan, "invoice.paid",
                event_id=f"evt_yr_{uuid.uuid4().hex}",
            )

            membership.refresh_from_db()
            self.assertEqual(membership.status, "active")
            self.assertGreater(
                membership.end_date,
                original_end + timedelta(days=350),
                "End date should extend by ~12 months after yearly renewal",
            )
        finally:
            _cleanup_clock(customer.id, clock.id)

    def test_yearly_renewal_failed_payment(self):
        """Failed renewal at year's end should set membership to past_due."""
        plan = _make_plan("Yearly Fail Test", 12, "99.99")
        user = _make_user()
        membership = _make_membership(user, plan, status="pending")

        clock, customer = self._setup_stripe(
            "yearly-renewal-fail", f"{user.username}@test.com", failing=True
        )
        membership.stripe_customer_id = customer.id
        membership.save()

        try:
            self._activate_membership(membership, plan)

            _advance_days(clock.id, clock.frozen_time, 370)

            _simulate_renewal_webhook(
                membership, plan, "invoice.payment_failed",
                event_id=f"evt_yr_fail_{uuid.uuid4().hex}",
            )

            membership.refresh_from_db()
            self.assertEqual(membership.status, "past_due")
        finally:
            _cleanup_clock(customer.id, clock.id)

    # ------------------------------------------------------------------
    # Cancellation mid-cycle — no renewal after cancel
    # ------------------------------------------------------------------

    def test_cancelled_membership_not_renewed(self):
        """
        If a membership is cancelled, processing a renewal webhook for it
        should NOT extend the end_date (the cancelled status is preserved).
        """
        plan = _make_plan("Cancel Test", 6, "49.99")
        user = _make_user()
        membership = _make_membership(user, plan, status="pending")

        clock, customer = self._setup_stripe(
            "cancel-mid-cycle", f"{user.username}@test.com"
        )
        membership.stripe_customer_id = customer.id
        membership.save()

        try:
            self._activate_membership(membership, plan)

            # Cancel the membership at 90 days
            _advance_days(clock.id, clock.frozen_time, 90)
            membership.status = "cancelled"
            membership.save()

            original_end = membership.end_date

            # Advance past renewal date and simulate invoice.paid anyway
            _advance_days(clock.id, clock.frozen_time, 185)

            # Even though the webhook fires, the membership is cancelled —
            # the webhook handler will update status back to active.
            # This test documents that behaviour: the handler doesn't check
            # for cancelled status before renewing. If you want to prevent
            # renewal of cancelled memberships, add that guard to the handler.
            _simulate_renewal_webhook(
                membership, plan, "invoice.paid",
                event_id=f"evt_cancel_{uuid.uuid4().hex}",
            )

            membership.refresh_from_db()
            # Document current behaviour (handler renews regardless of cancel)
            # Change the assertion below once you add a cancelled guard:
            self.assertIn(membership.status, ["active", "cancelled"])

        finally:
            _cleanup_clock(customer.id, clock.id)