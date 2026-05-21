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

def get_or_create_product():
    products = stripe.Product.search(query="name:'SwiftCart Membership'", limit=1)
    if products.data:
        return products.data[0]
    return stripe.Product.create(name="SwiftCart Membership")

def create_subscription(customer_id, amount_cents, duration_months, metadata=None):
    product = get_or_create_product()
    interval = "month"
    interval_count = duration_months
    if duration_months == 12:
        interval_count = 1
        interval = "year"
        
    subscription = stripe.Subscription.create(
        customer=customer_id,
        items=[{
            "price_data": {
                "currency": "usd",
                "product": product.id,
                "unit_amount": amount_cents,
                "recurring": {
                    "interval": interval,
                    "interval_count": interval_count,
                }
            }
        }],
        payment_behavior="default_incomplete",
        payment_settings={"save_default_payment_method": "on_subscription"},
        expand=["latest_invoice.payments"],
        metadata=metadata or {}
    )
    
    client_secret = None
    intent_id = None
    
    if subscription.latest_invoice and subscription.latest_invoice.payments:
        payments_data = subscription.latest_invoice.payments.data
        if payments_data and hasattr(payments_data[0], 'payment') and payments_data[0].payment.type == "payment_intent":
            intent_id = payments_data[0].payment.payment_intent
            intent = stripe.PaymentIntent.retrieve(intent_id)
            client_secret = intent.client_secret
            if metadata:
                stripe.PaymentIntent.modify(intent_id, metadata=metadata)
            
    return {
        "subscription_id": subscription.id,
        "client_secret": client_secret,
        "payment_intent_id": intent_id
    }

def verify_webhook(payload, sig_header):
    return stripe.Webhook.construct_event(
        payload,
        sig_header,
        settings.STRIPE_MEMBERSHIP_WEBHOOK_SECRET
    )

def cancel_subscription(subscription_id):
    return stripe.Subscription.delete(subscription_id)

def cancel_subscription_immediately(subscription_id):
    return stripe.Subscription.delete(subscription_id)

def create_payment_intent(customer_id, amount_cents, metadata=None):
    return stripe.PaymentIntent.create(
        amount=amount_cents,
        currency="usd",
        customer=customer_id,
        payment_method_types=["card"],
        metadata=metadata or {}
    )

def retrieve_subscription(subscription_id):
    return stripe.Subscription.retrieve(subscription_id)

def update_subscription_price(subscription_id, new_price_id):
    subscription = stripe.Subscription.retrieve(subscription_id)
    item_id = subscription['items']['data'][0].id
    return stripe.Subscription.modify(
        subscription_id,
        items=[{
            'id': item_id,
            'price': new_price_id,
        }],
        proration_behavior='always_invoice'
    )
