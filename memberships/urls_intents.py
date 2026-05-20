from django.urls import path
from . import views_intents

app_name = "memberships"

urlpatterns = [
    path("plans/", views_intents.PlansPageView.as_view(), name="plans"),
    path("dashboard/", views_intents.MembershipDashboardView.as_view(), name="dashboard"),
    path("subscribe/", views_intents.CreateSubscriptionView.as_view(), name="subscribe"),
    path("success/", views_intents.MembershipSuccessView.as_view(), name="success"),
    path("webhook/", views_intents.StripeWebhookView.as_view(), name="webhook"),
    path("api/status/", views_intents.MembershipStatusView.as_view(), name="api-status"),
    path("api/cancel/", views_intents.CancelMembershipView.as_view(), name="api-cancel"),
]
