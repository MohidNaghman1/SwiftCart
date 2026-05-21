from django.contrib import admin

from .models import MembershipPayment, MembershipPlan, UserMembership


@admin.register(MembershipPlan)
class MembershipPlanAdmin(admin.ModelAdmin):
    list_display = ("name", "duration_months", "price", "is_active")
    list_filter = ("is_active",)


@admin.register(UserMembership)
class UserMembershipAdmin(admin.ModelAdmin):
    list_display = ("user", "plan", "status", "start_date", "end_date")
    list_filter = ("status",)
    search_fields = ("user__email",)
    readonly_fields = (
        "stripe_subscription_id",
        "stripe_customer_id",
        "created_at",
        "updated_at",
    )


@admin.register(MembershipPayment)
class MembershipPaymentAdmin(admin.ModelAdmin):
    list_display = ("user", "amount", "status", "created_at")
    list_filter = ("status",)
    search_fields = ("user__email",)
    readonly_fields = ("stripe_event_id", "created_at")
