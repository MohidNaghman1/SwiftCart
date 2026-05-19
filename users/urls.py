from django.urls import path

from .views import CustomTokenObtainPairView, CustomTokenRefreshView, RegisterView

# Expose the three user authentication endpoints.
urlpatterns = [
    path('api/users/register/', RegisterView.as_view(), name='user-register'),
    path('api/users/login/', CustomTokenObtainPairView.as_view(), name='token-obtain-pair'),
    path('api/users/token/refresh/', CustomTokenRefreshView.as_view(), name='token-refresh'),
]