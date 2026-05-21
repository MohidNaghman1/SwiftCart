"""Root URL configuration for Swiftcart."""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from .views import (
    AdminCategoriesPageView,
    AdminDashboardView,
    AdminMembershipsPageView,
    AdminOrdersPageView,
    AdminProductsPageView,
    AdminUsersPageView,
    CancelPageView,
    CheckoutPageView,
    HomeView,
    LoginPageView,
    ProductsPageView,
    RegisterPageView,
    SuccessPageView,
)


urlpatterns = [
    path('', HomeView.as_view(), name='home'),
    path('register/', RegisterPageView.as_view(), name='register'),
    path('login/', LoginPageView.as_view(), name='login'),
    path('products/', ProductsPageView.as_view(), name='products'),
    path('checkout/', CheckoutPageView.as_view(), name='checkout'),
    path('success/', SuccessPageView.as_view(), name='success'),
    path('cancel/', CancelPageView.as_view(), name='cancel'),
    path('admin-dashboard/', AdminDashboardView.as_view(), name='admin-dashboard'),
    path('admin-dashboard/memberships/', AdminMembershipsPageView.as_view(), name='admin-memberships'),
    path('admin-dashboard/products/', AdminProductsPageView.as_view(), name='admin-products'),
    path('admin-dashboard/categories/', AdminCategoriesPageView.as_view(), name='admin-categories'),
    path('admin-dashboard/orders/', AdminOrdersPageView.as_view(), name='admin-orders'),
    path('admin-dashboard/users/', AdminUsersPageView.as_view(), name='admin-users'),
    path('admin/', admin.site.urls),
    path('', include('users.urls')),
    path('', include('products.urls')),
    path('', include('orders.urls')),
    path('memberships/', include('memberships.urls_intents')),
]

urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
