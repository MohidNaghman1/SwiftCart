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

        from Swiftcart.utils import convert_pkr_to_usd_cents
        usd_cents = convert_pkr_to_usd_cents(plan.price)
        if usd_cents < 50:
            usd_cents = 50

        customer = stripe_service.get_or_create_customer(request.user)
        
        metadata = {
            "purpose": "membership",
            "user_id": str(request.user.id),
            "plan_id": str(plan.id),
            "price": str(plan.price),
        }

        # Create payment intent
        intent = stripe_service.create_payment_intent(customer.id, usd_cents, metadata=metadata)

        membership, _ = UserMembership.objects.get_or_create(user=request.user)
        membership.plan = plan
        membership.stripe_customer_id = customer.id
        membership.status = "pending"
        membership.save()

        return JsonResponse({
            "client_secret": intent.client_secret,
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

        if event_type == "payment_intent.succeeded":
            intent = event.data.object
            metadata = getattr(intent, 'metadata', None)
            if metadata and getattr(metadata, 'purpose', None) == "membership":
                user_id = getattr(metadata, 'user_id', None)
                plan_id = getattr(metadata, 'plan_id', None)
                try:
                    membership = UserMembership.objects.get(user_id=user_id)
                    plan = MembershipPlan.objects.get(id=plan_id)
                    membership.plan = plan
                    membership.status = "active"
                    if not membership.start_date:
                        membership.start_date = now()
                    membership.end_date = now() + relativedelta(months=plan.duration_months)
                    membership.save()

                    MembershipPayment.objects.create(
                        user=membership.user,
                        membership=membership,
                        amount=plan.price,
                        status="success",
                        stripe_event_id=event.id
                    )
                    logger.info(f"Stripe webhook: membership activated for {user_id}")
                except (UserMembership.DoesNotExist, MembershipPlan.DoesNotExist):
                    pass

        elif event_type == "payment_intent.payment_failed":
            intent = event.data.object
            metadata = getattr(intent, 'metadata', None)
            if metadata and getattr(metadata, 'purpose', None) == "membership":
                user_id = getattr(metadata, 'user_id', None)
                try:
                    membership = UserMembership.objects.get(user_id=user_id)
                    membership.status = "past_due"
                    membership.save()

                    MembershipPayment.objects.create(
                        user=membership.user,
                        membership=membership,
                        amount=0,
                        status="failed",
                        stripe_event_id=event.id
                    )
                    logger.info(f"Stripe webhook: payment failed for {user_id}")
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
            "price": str(membership.plan.price) if membership.plan else None,
            "currency": "PKR" if membership.plan else None
        })

class CancelMembershipView(LoginRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        try:
            membership = UserMembership.objects.get(user=request.user)
        except UserMembership.DoesNotExist:
            return JsonResponse({"error": "Not found"}, status=404)

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
            f"Your remaining credit of PKR {result['unused_value']} will be applied. "
            f"You pay PKR {result['amount_due']} today."
            if result["is_eligible"]
            else
            f"Not eligible. Your remaining credit (PKR {result['unused_value']}) "
            f"exceeds the {new_plan.name} plan price (PKR {result['new_plan_price']}). "
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

        result = plan_switch_service.calculate_switch_cost(membership, new_plan)

        # As plan switches are now handled differently without Stripe Subscriptions,
        # we can reject the switch directly here if it costs money to avoid breaking the UI.
        if result["amount_due"] > 0:
            return JsonResponse({"error": "Plan upgrades require payment. Please cancel your current plan and subscribe to the new one."}, status=400)

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



