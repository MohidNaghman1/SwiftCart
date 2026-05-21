"""
To run all billing cycle tests:
  python manage.py test memberships.tests.test_billing_cycles

To run one specific test:
  python manage.py test memberships.tests.test_billing_cycles.TestMonthlyBillingCycle.test_successful_monthly_renewal

These tests hit the real Stripe sandbox API.
STRIPE_SECRET_KEY in .env must be a test key (sk_test_...).
Each test creates and cleans up its own Stripe objects.
Tests may take 30-60 seconds each due to clock polling.
"""

import stripe
from decimal import Decimal
from datetime import timedelta
from django.conf import settings
from django.test import TestCase
from django.utils import timezone

from memberships.tests.stripe_test_helpers import (
    advance_days,
    attach_failing_payment_method,
    attach_payment_method,
    cleanup,
    create_customer_with_clock,
    create_subscription,
    create_test_clock,
    get_subscription_invoices,
    get_subscription_status,
    pay_open_invoices,
)

stripe.api_key = settings.STRIPE_SECRET_KEY

MONTHLY_PRICE_ID    = settings.STRIPE_MONTHLY_PRICE_ID
SIX_MONTH_PRICE_ID  = settings.STRIPE_SIX_MONTH_PRICE_ID
YEARLY_PRICE_ID     = settings.STRIPE_YEARLY_PRICE_ID


class TestMonthlyBillingCycle(TestCase):

    def test_successful_monthly_renewal(self):
        clock    = create_test_clock("monthly-renewal-success")
        customer = create_customer_with_clock("test_monthly@test.com", clock.id)
        attach_payment_method(customer.id)
        sub      = create_subscription(customer.id, MONTHLY_PRICE_ID)

        self.assertEqual(sub.status, "active")

        frozen_time = clock.frozen_time
        advance_days(clock.id, frozen_time, 31)
        pay_open_invoices(sub.id)

        invoices = get_subscription_invoices(sub.id)
        self.assertGreaterEqual(len(invoices.data), 2)
        self.assertTrue(all(inv.status == "paid" for inv in invoices.data))
        self.assertEqual(get_subscription_status(sub.id), "active")

        for inv in invoices.data:
            print(f"Invoice: ${inv.amount_due/100:.2f} | {inv.status} | {inv.created}")

        cleanup(customer.id, clock.id)

    def test_failed_monthly_renewal(self):
        clock    = create_test_clock("monthly-renewal-fail")
        customer = create_customer_with_clock("test_monthly_fail@test.com", clock.id)
        attach_payment_method(customer.id)
        sub      = create_subscription(customer.id, MONTHLY_PRICE_ID)

        self.assertEqual(sub.status, "active")

        frozen_time = clock.frozen_time
        attach_failing_payment_method(customer.id)
        advance_days(clock.id, frozen_time, 31)

        status = get_subscription_status(sub.id)
        self.assertIn(status, ["past_due", "unpaid"])

        invoices = get_subscription_invoices(sub.id)
        failed   = [inv for inv in invoices.data if inv.status != "paid"]
        self.assertGreaterEqual(len(failed), 1)

        for inv in invoices.data:
            print(f"Invoice: {inv.status} | attempts: {inv.attempt_count}")

        cleanup(customer.id, clock.id)


class TestSixMonthBillingCycle(TestCase):

    def test_six_month_renewal(self):
        clock    = create_test_clock("six-month-renewal")
        customer = create_customer_with_clock("test_6mo@test.com", clock.id)
        attach_payment_method(customer.id)
        sub      = create_subscription(customer.id, SIX_MONTH_PRICE_ID)

        self.assertEqual(sub.status, "active")

        frozen_time = clock.frozen_time
        advance_days(clock.id, frozen_time, 90)

        invoices_mid = get_subscription_invoices(sub.id)
        self.assertEqual(len(invoices_mid.data), 1)

        advance_days(clock.id, frozen_time, 185)
        pay_open_invoices(sub.id)

        invoices_after = get_subscription_invoices(sub.id)
        self.assertGreaterEqual(len(invoices_after.data), 2)
        self.assertEqual(get_subscription_status(sub.id), "active")

        print(f"Mid (90d):  {len(invoices_mid.data)} invoice(s)")
        print(f"After (185d): {len(invoices_after.data)} invoice(s)")

        cleanup(customer.id, clock.id)

    def test_six_month_cancellation_mid_cycle(self):
        clock    = create_test_clock("six-month-cancel")
        customer = create_customer_with_clock("test_6mo_cancel@test.com", clock.id)
        attach_payment_method(customer.id)
        sub      = create_subscription(customer.id, SIX_MONTH_PRICE_ID)

        self.assertEqual(sub.status, "active")

        frozen_time = clock.frozen_time
        advance_days(clock.id, frozen_time, 90)

        stripe.Subscription.delete(sub.id)

        advance_days(clock.id, frozen_time, 185)

        self.assertEqual(get_subscription_status(sub.id), "canceled")

        invoices = get_subscription_invoices(sub.id)
        self.assertEqual(len(invoices.data), 1)

        cleanup(customer.id, clock.id)


class TestYearlyBillingCycle(TestCase):

    def test_yearly_renewal(self):
        clock    = create_test_clock("yearly-renewal")
        customer = create_customer_with_clock("test_yearly@test.com", clock.id)
        attach_payment_method(customer.id)
        sub      = create_subscription(customer.id, YEARLY_PRICE_ID)

        self.assertEqual(sub.status, "active")

        frozen_time = clock.frozen_time
        advance_days(clock.id, frozen_time, 180)

        self.assertEqual(get_subscription_status(sub.id), "active")
        self.assertEqual(len(get_subscription_invoices(sub.id).data), 1)

        advance_days(clock.id, frozen_time, 370)
        pay_open_invoices(sub.id)

        self.assertGreaterEqual(len(get_subscription_invoices(sub.id).data), 2)
        self.assertEqual(get_subscription_status(sub.id), "active")

        cleanup(customer.id, clock.id)

    def test_yearly_failed_renewal(self):
        clock    = create_test_clock("yearly-renewal-fail")
        customer = create_customer_with_clock("test_yearly_fail@test.com", clock.id)
        attach_payment_method(customer.id)
        sub      = create_subscription(customer.id, YEARLY_PRICE_ID)

        self.assertEqual(sub.status, "active")

        frozen_time = clock.frozen_time
        attach_failing_payment_method(customer.id)
        advance_days(clock.id, frozen_time, 370)

        self.assertIn(get_subscription_status(sub.id), ["past_due", "unpaid"])

        cleanup(customer.id, clock.id)


class TestPlanSwitch(TestCase):

    def test_upgrade_mid_cycle(self):
        clock    = create_test_clock("upgrade-mid-cycle")
        customer = create_customer_with_clock("test_upgrade@test.com", clock.id)
        attach_payment_method(customer.id)
        sub      = create_subscription(customer.id, SIX_MONTH_PRICE_ID)

        self.assertEqual(sub.status, "active")

        frozen_time = clock.frozen_time
        advance_days(clock.id, frozen_time, 90)
        self.assertEqual(get_subscription_status(sub.id), "active")

        stripe.Subscription.delete(sub.id)

        new_sub = create_subscription(customer.id, YEARLY_PRICE_ID)
        self.assertEqual(get_subscription_status(new_sub.id), "active")

        print("Old sub invoices:", len(get_subscription_invoices(sub.id).data))
        print("New sub invoices:", len(get_subscription_invoices(new_sub.id).data))

        cleanup(customer.id, clock.id)

    def test_downgrade_not_eligible(self):
        from memberships.services.plan_switch_service import calculate_switch_cost
        from memberships.models import MembershipPlan, UserMembership
        from django.contrib.auth import get_user_model

        User = get_user_model()

        from_plan = MembershipPlan.objects.create(
            name="6-Month",
            duration_months=6,
            price=Decimal("49.99"),
            stripe_price_id="price_test_6month",
            is_active=True
        )
        to_plan = MembershipPlan.objects.create(
            name="Monthly",
            duration_months=1,
            price=Decimal("9.99"),
            stripe_price_id="price_test_monthly",
            is_active=True
        )

        user = User.objects.create_user(
            username="test_downgrade",
            email="test_downgrade@test.com",
            password="testpass123"
        )

        membership = UserMembership.objects.create(
            user=user,
            plan=from_plan,
            status="active",
            start_date=timezone.now() - timedelta(days=30),
            end_date=timezone.now() + timedelta(days=150)
        )

        result = calculate_switch_cost(membership, to_plan)

        self.assertFalse(result["is_eligible"])
        self.assertGreater(result["unused_value"], to_plan.price)

        print(f"Unused value: ${result['unused_value']}")
        print(f"New plan price: ${result['new_plan_price']}")
        print(f"Eligible: {result['is_eligible']}")