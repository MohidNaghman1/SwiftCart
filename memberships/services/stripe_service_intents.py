import stripe
from django.conf import settings

stripe.api_key = settings.STRIPE_SECRET_KEY

def get_or_create_customer(user):
    customers = stripe.Customer.list(email=user.email, limit=1)
    if customers.data:
        return customers.data[0]
    return stripe.Customer.create(
        email=user.email,
        metadata={"user_id": str(user.id)}
    )

def create_subscription(customer_id, stripe_price_id):
    subscription = stripe.Subscription.create(
        customer=customer_id,
        items=[{"price": stripe_price_id}],
        payment_behavior="default_incomplete",
        payment_settings={"save_default_payment_method": "on_subscription"},
        expand=["latest_invoice.payment_intent"]
    )
    return {
        "subscription_id": subscription.id,
        "client_secret": subscription.latest_invoice.payment_intent.client_secret,
        "payment_intent_id": subscription.latest_invoice.payment_intent.id
    }

def verify_webhook(payload, sig_header):
    return stripe.Webhook.construct_event(
        payload,
        sig_header,
        settings.STRIPE_MEMBERSHIP_WEBHOOK_SECRET
    )

def cancel_subscription(subscription_id):
    return stripe.Subscription.delete(subscription_id)

def retrieve_subscription(subscription_id):
    return stripe.Subscription.retrieve(subscription_id)
