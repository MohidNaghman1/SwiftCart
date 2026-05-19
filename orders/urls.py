from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
	OrderViewSet,
	StripeCancelView,
	StripeCheckoutInitView,
	StripeSuccessView,
	StripeWebhookView,
	OrderStatusPollView,
)

router = DefaultRouter()
router.register(r'', OrderViewSet, basename='orders')

urlpatterns = [
	path('api/orders/create-checkout/', StripeCheckoutInitView.as_view(), name='order-checkout-init'),
	path('api/orders/success/', StripeSuccessView.as_view(), name='stripe-success'),
	path('api/orders/cancel/', StripeCancelView.as_view(), name='stripe-cancel'),
	path('api/orders/webhook/', StripeWebhookView.as_view(), name='stripe-webhook'),
	path('api/orders/status/<str:session_id>/', OrderStatusPollView.as_view(), name='order-status-poll'),
	path('api/orders/', include(router.urls)),
]