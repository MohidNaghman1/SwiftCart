import logging
import stripe
from dateutil.relativedelta import relativedelta

from django.conf import settings
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponse, JsonResponse
from django.utils.decorators import method_decorator
from django.utils.timezone import now
from django.views import View
from django.views.generic import TemplateView
from django.views.decorators.csrf import csrf_exempt

from memberships.models import MembershipPayment, MembershipPlan, UserMembership
from memberships.services import stripe_service_intents as stripe_service

logger = logging.getLogger("memberships")

class PlansPageView(TemplateView):
    template_name = "memberships/plans.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["plans"] = MembershipPlan.objects.filter(is_active=True).order_by("price")
        context["stripe_publishable_key"] = settings.STRIPE_PUBLISHABLE_KEY
        return context

class MembershipDashboardView(LoginRequiredMixin, TemplateView):
    template_name = "memberships/dashboard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        membership, _ = UserMembership.objects.get_or_create(user=self.request.user)
        context["membership"] = membership
        return context

class CreateSubscriptionView(LoginRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        plan_id = request.POST.get("plan_id")
        try:
            plan = MembershipPlan.objects.get(id=plan_id)
        except MembershipPlan.DoesNotExist:
            return JsonResponse({"error": "Plan not found"}, status=404)

        customer = stripe_service.get_or_create_customer(request.user)
        result = stripe_service.create_subscription(customer.id, plan.stripe_price_id)

        membership, _ = UserMembership.objects.get_or_create(user=request.user)
        membership.plan = plan
        membership.stripe_customer_id = customer.id
        membership.stripe_subscription_id = result["subscription_id"]
        membership.status = "pending"
        membership.save()

        return JsonResponse({
            "client_secret": result["client_secret"],
            "subscription_id": result["subscription_id"]
        })

class MembershipSuccessView(LoginRequiredMixin, TemplateView):
    template_name = "memberships/success.html"

@method_decorator(csrf_exempt, name='dispatch')
class StripeWebhookView(View):
    def post(self, request, *args, **kwargs):
        payload = request.body
        sig_header = request.META.get("HTTP_STRIPE_SIGNATURE", "")

        try:
            event = stripe_service.verify_webhook(payload, sig_header)
        except stripe.error.SignatureVerificationError:
            return HttpResponse(status=400)

        event_type = event.type

        if event_type == "invoice.payment_succeeded":
            subscription_id = event.data.object.subscription
            try:
                membership = UserMembership.objects.get(stripe_subscription_id=subscription_id)
                membership.status = "active"
                if not membership.start_date:
                    membership.start_date = now()
                membership.end_date = now() + relativedelta(months=membership.plan.duration_months)
                membership.save()

                MembershipPayment.objects.create(
                    user=membership.user,
                    membership=membership,
                    amount=event.data.object.amount_paid / 100,
                    status="success",
                    stripe_event_id=event.id
                )
                logger.info(f"Stripe webhook: {event_type}")
            except UserMembership.DoesNotExist:
                pass

        elif event_type == "invoice.payment_failed":
            subscription_id = event.data.object.subscription
            try:
                membership = UserMembership.objects.get(stripe_subscription_id=subscription_id)
                membership.status = "past_due"
                membership.save()

                MembershipPayment.objects.create(
                    user=membership.user,
                    membership=membership,
                    amount=0,
                    status="failed",
                    stripe_event_id=event.id
                )
                logger.info(f"Stripe webhook: {event_type}")
            except UserMembership.DoesNotExist:
                pass

        elif event_type == "customer.subscription.deleted":
            subscription_id = event.data.object.subscription
            try:
                membership = UserMembership.objects.get(stripe_subscription_id=subscription_id)
                membership.status = "cancelled"
                membership.save()
                logger.info(f"Stripe webhook: {event_type}")
            except UserMembership.DoesNotExist:
                pass

        return HttpResponse(status=200)

class MembershipStatusView(LoginRequiredMixin, View):
    def get(self, request, *args, **kwargs):
        try:
            membership = UserMembership.objects.get(user=request.user)
        except UserMembership.DoesNotExist:
            return JsonResponse({"error": "Not found"}, status=404)

        return JsonResponse({
            "status": membership.status,
            "plan_name": membership.plan.name if membership.plan else None,
            "end_date": membership.end_date.isoformat() if membership.end_date else None,
            "price": str(membership.plan.price) if membership.plan else None
        })

class CancelMembershipView(LoginRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        try:
            membership = UserMembership.objects.get(user=request.user)
        except UserMembership.DoesNotExist:
            return JsonResponse({"error": "Not found"}, status=404)

        if not membership.stripe_subscription_id:
            return JsonResponse({"error": "No active subscription"}, status=400)

        stripe_service.cancel_subscription(membership.stripe_subscription_id)
        membership.status = "cancelled"
        membership.save()

        return JsonResponse({"success": True})
