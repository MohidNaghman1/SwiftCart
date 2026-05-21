import logging
import stripe
from dateutil.relativedelta import relativedelta
from django.utils import timezone
from django.conf import settings
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponse, JsonResponse
from django.utils.decorators import method_decorator
from django.utils.timezone import now
from django.views import View
from django.views.generic import TemplateView
from django.views.decorators.csrf import csrf_exempt

from memberships.models import MembershipPayment, MembershipPlan, UserMembership, PlanSwitchRecord
from memberships.services import plan_switch_service
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
        context["plans"] = MembershipPlan.objects.filter(is_active=True).order_by("price")
        context["stripe_publishable_key"] = settings.STRIPE_PUBLISHABLE_KEY
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
        logger.info(f"Received Stripe webhook: {event_type}")

        if event_type == "invoice.payment_succeeded":
            invoice = event.data.object
            subscription_id = getattr(invoice, "subscription", None)
            if not subscription_id and getattr(invoice, "parent", None) and getattr(invoice.parent, "subscription_details", None):
                subscription_id = getattr(invoice.parent.subscription_details, "subscription", None)
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
            invoice = event.data.object
            subscription_id = getattr(invoice, "subscription", None)
            if not subscription_id and getattr(invoice, "parent", None) and getattr(invoice.parent, "subscription_details", None):
                subscription_id = getattr(invoice.parent.subscription_details, "subscription", None)
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
            subscription_id = event.data.object.id
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


class SwitchPlanPreviewView(LoginRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        new_plan_id = request.POST.get("new_plan_id")
        try:
            new_plan = MembershipPlan.objects.get(id=new_plan_id)
            membership = UserMembership.objects.get(user=request.user)
        except (MembershipPlan.DoesNotExist, UserMembership.DoesNotExist):
            return JsonResponse({"error": "Not found"}, status=404)

        if membership.status != "active":
            return JsonResponse({"error": "No active membership to switch from"}, status=400)
        
        if membership.plan and str(new_plan.id) == str(membership.plan.id):
            return JsonResponse({"error": "You are already on this plan"}, status=400)

        result = plan_switch_service.calculate_switch_cost(membership, new_plan)
        
        message = (
            f"Your remaining credit of ${result['unused_value']} will be applied. "
            f"You pay ${result['amount_due']} today."
            if result["is_eligible"]
            else
            f"Not eligible. Your remaining credit (${result['unused_value']}) "
            f"exceeds the {new_plan.name} plan price (${result['new_plan_price']}). "
            f"No refund is issued."
        )

        from django.utils import timezone
        return JsonResponse({
            "is_eligible": result["is_eligible"],
            "is_free": result["is_free"],
            "unused_value": str(result["unused_value"]),
            "new_plan_price": str(result["new_plan_price"]),
            "amount_due": str(result["amount_due"]),
            "new_plan_name": new_plan.name,
            "billing_label": plan_switch_service.get_billing_label(new_plan.duration_months),
            "days_remaining": (membership.end_date - timezone.now()).days if membership.end_date else 0,
            "message": message
        })


class SwitchPlanConfirmView(LoginRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        new_plan_id = request.POST.get("new_plan_id")
        try:
            new_plan = MembershipPlan.objects.get(id=new_plan_id)
            membership = UserMembership.objects.get(user=request.user)
        except (MembershipPlan.DoesNotExist, UserMembership.DoesNotExist):
            return JsonResponse({"error": "Not found"}, status=404)

        if not membership.stripe_subscription_id:
            return JsonResponse({"error": "No active Stripe subscription"}, status=400)

        result = plan_switch_service.calculate_switch_cost(membership, new_plan)
        if not result["is_eligible"]:
            return JsonResponse({"error": "Not eligible to switch to this plan"}, status=400)

        
        try:
            stripe_service.update_subscription_price(membership.stripe_subscription_id, new_plan.stripe_price_id)
        except stripe.error.StripeError as e:
            return JsonResponse({"error": str(e)}, status=400)

        from_plan = membership.plan
        membership.plan = new_plan
        membership.end_date = timezone.now() + relativedelta(months=new_plan.duration_months)
        membership.save()

        PlanSwitchRecord.objects.create(
            user=request.user,
            from_plan=from_plan,
            to_plan=new_plan,
            amount_paid=result["amount_due"],
            credit_applied=result["unused_value"]
        )

        return JsonResponse({"success": True, "requires_payment": False})



