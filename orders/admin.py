from django.contrib import admin
from .models import Order, OrderItem


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    readonly_fields = ('product', 'quantity', 'price_at_purchase')
    can_delete = False


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    inlines = [OrderItemInline]  # ← shows all items inside order

    list_display = ('id', 'user', 'status', 'stripe_session_id', 'payment_method', 'created_at')
    list_filter = ('status',)
    search_fields = ('user__username',)
    readonly_fields = ('stripe_session_id', 'payment_method', 'created_at')


@admin.register(OrderItem)
class OrderItemAdmin(admin.ModelAdmin):
    list_display = ('order', 'product', 'quantity', 'price_at_purchase')
    readonly_fields = ('price_at_purchase',)