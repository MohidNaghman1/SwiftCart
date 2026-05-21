import logging
import stripe
from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from dateutil.relativedelta import relativedelta

from memberships.models import UserMembership, MembershipPlan
from django.conf import settings

# Import your currency converter utility
from Swiftcart.utils import convert_pkr_to_usd_cents

logger = logging.getLogger("memberships")

class Command(BaseCommand):
    help = 'Checks for expiring memberships and attempts to renew them using off-session PaymentIntents.'

    def handle(self, *args, **options):
        # 1. Find memberships expiring in the next 3 days
        # We use a buffer (e.g., 3 days) to retry if the first attempt fails, 
        # or to renew a day early to prevent service interruption.
        renewal_window_start = timezone.now()
        renewal_window_end = timezone.now() + timedelta(days=3)

        expiring_memberships = UserMembership.objects.filter(
            status='active',
            end_date__gte=renewal_window_start,
            end_date__lte=renewal_window_end,
            stripe_customer_id__isnull=False, # Must have a saved customer
            plan__isnull=False
        ).select_related('plan', 'user')

        self.stdout.write(f"Found {expiring_memberships.count()} memberships up for renewal.")

        for membership in expiring_memberships:
            self.renew_membership(membership)

    def renew_membership(self, membership):
        user = membership.user
        plan = membership.plan
        customer_id = membership.stripe_customer_id

        try:
            # 2. Retrieve the Customer from Stripe to find their default payment method
            customer = stripe.Customer.retrieve(customer_id)
            pm_id = customer.invoice_settings.default_payment_method

            if not pm_id:
                logger.warning(f"User {user.id} has no default payment method. Skipping renewal.")
                return

            # 3. Calculate Amount (Convert PKR to USD Cents)
            # Note: Ensure this function handles decimals correctly
            usd_cents = convert_pkr_to_usd_cents(plan.price)
            if usd_cents < 50:
                usd_cents = 50 # Stripe minimum

            # 4. Create the Metadata
            # THIS IS CRITICAL: This matches the metadata expected by your Webhook View.
            # When Stripe charges this, it fires payment_intent.succeeded, and your
            # webhook will see this metadata and extend the end_date automatically.
            metadata = {
                "purpose": "membership",
                "user_id": str(user.id),
                "plan_id": str(plan.id),
                "price": str(plan.price),
            }

            # 5. Create and Confirm the PaymentIntent OFF-SESSION
            # 'off_session=True' tells Stripe to charge the card without the user 
            # being present on the website.
            intent = stripe.PaymentIntent.create(
                amount=usd_cents,
                currency='usd',
                customer=customer_id,
                payment_method=pm_id,
                confirm=True,
                off_session=True,
                metadata=metadata,
                description=f"Auto-renewal for {plan.name}",
                # Prevent duplicate creation if this command runs twice
                idempotency_key=f"renewal_{membership.id}_{membership.end_date.strftime('%Y%m%d')}" 
            )

            logger.info(f"Successfully triggered renewal charge for user {user.id}. Intent ID: {intent.id}")
            self.stdout.write(self.style.SUCCESS(f"Renewed user {user.username} (Intent: {intent.id})"))

        except stripe.error.CardError as e:
            # Card was declined (Insufficient funds, expired, etc.)
            # The webhook will eventually receive 'payment_intent.payment_failed'
            # and set status to 'past_due'.
            logger.error(f"Card declined for user {user.id}: {e.err}")
            self.stdout.write(self.style.ERROR(f"Card declined for {user.username}: {e.err}"))
        
        except Exception as e:
            logger.error(f"Failed to renew membership for user {user.id}: {str(e)}")
            self.stdout.write(self.style.ERROR(f"Error renewing {user.username}: {str(e)}"))