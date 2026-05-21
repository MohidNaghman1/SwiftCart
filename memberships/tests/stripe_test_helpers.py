import stripe
from datetime import datetime
from django.conf import settings
import time

stripe.api_key = settings.STRIPE_SECRET_KEY

def create_test_clock(name):
    return stripe.test_helpers.TestClock.create(
        frozen_time=int(datetime.utcnow().timestamp()),
        name=name
    )

def create_customer_with_clock(email, test_clock_id):
    return stripe.Customer.create(
        email=email,
        test_clock=test_clock_id
    )

def attach_payment_method(customer_id):
    pm = stripe.PaymentMethod.create(
        type="card",
        card={"token": "tok_visa"}
    )
    stripe.PaymentMethod.attach(pm.id, customer=customer_id)
    stripe.Customer.modify(
        customer_id,
        invoice_settings={"default_payment_method": pm.id}
    )
    return pm

def attach_failing_payment_method(customer_id):
    pm = stripe.PaymentMethod.create(
        type="card",
        card={"token": "tok_chargeCustomerFail"}
    )
    stripe.PaymentMethod.attach(pm.id, customer=customer_id)
    stripe.Customer.modify(
        customer_id,
        invoice_settings={"default_payment_method": pm.id}
    )
    return pm

def create_subscription(customer_id, price_id):
    sub = stripe.Subscription.create(
        customer=customer_id,
        items=[{"price": price_id}],
        collection_method="charge_automatically",
    )
    return sub

def pay_open_invoices(subscription_id):
    invoices = stripe.Invoice.list(subscription=subscription_id, status="open")
    for invoice in invoices.data:
        try:
            stripe.Invoice.pay(invoice.id)
        except stripe.error.InvalidRequestError:
            pass

def advance_clock(test_clock_id, advance_to_timestamp):
    stripe.test_helpers.TestClock.advance(
        test_clock_id,
        frozen_time=int(advance_to_timestamp)
    )
    for _ in range(20):
        time.sleep(2)
        clock = stripe.test_helpers.TestClock.retrieve(test_clock_id)
        if clock.status == "ready":
            time.sleep(3)
            return clock
    raise TimeoutError("Test clock did not advance in time")

def get_subscription_invoices(subscription_id):
    return stripe.Invoice.list(subscription=subscription_id)

def get_subscription_status(subscription_id):
    return stripe.Subscription.retrieve(subscription_id).status

def cleanup(customer_id, test_clock_id):
    subs = stripe.Subscription.list(customer=customer_id)
    for sub in subs.data:
        try:
            stripe.Subscription.delete(sub.id)
        except stripe.error.InvalidRequestError:
            pass
    stripe.Customer.delete(customer_id)
    stripe.test_helpers.TestClock.delete(test_clock_id)

def advance_days(test_clock_id, frozen_time_timestamp, days):
    new_timestamp = int(frozen_time_timestamp) + (days * 86400)
    advance_clock(test_clock_id, new_timestamp)
    return new_timestamp