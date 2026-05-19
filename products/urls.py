from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import CategoryViewSet, ProductViewSet

# Register the products API routes.
router = DefaultRouter()
router.register(r'categories', CategoryViewSet, basename='categories')
router.register(r'', ProductViewSet, basename='products')

# Expose the product router under a single API prefix.
urlpatterns = [
    path('api/products/', include(router.urls)),
]