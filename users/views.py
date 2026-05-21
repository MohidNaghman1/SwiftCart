from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView
from django.contrib.auth import login

from Swiftcart.utils import api_response, build_absolute_uri
from .serializers import RegisterSerializer


# Mixin that provides user detail builder for all auth views.
class UserDetailMixin:

    def get_user_details(self, user, request=None):
        profile_image = user.profile_image.url if user.profile_image else None
        return {
            'id': user.id,
            'username': user.username,
            'email': user.email,
            'first_name': user.first_name,
            'last_name': user.last_name,
            'is_staff': user.is_staff,
            'profile_image': build_absolute_uri(request, profile_image) if request else profile_image,
        }


# Handle user registration.
class RegisterView(UserDetailMixin, APIView):
    permission_classes = [AllowAny]
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    # Create a user and return access and refresh tokens.
    def post(self, request, *args, **kwargs):
        payload = request.data.copy()
        if request.FILES.get('profile_image'):
            payload['profile_image'] = request.FILES['profile_image']

        serializer = RegisterSerializer(data=payload, context={'request': request})
        if not serializer.is_valid():
            return api_response(False, 'Registration failed', {'details': serializer.errors}, http_status=400)

        user = serializer.save()
        login(request, user)
        refresh = RefreshToken.for_user(user)
        return api_response(
            True,
            'Registration successful',
            {
                'details': self.get_user_details(user, request),
                'access_token': str(refresh.access_token),
                'refresh_token': str(refresh),
            },
            http_status=201,
        )


# Handle JWT login with a standardized response body.
class CustomTokenObtainPairView(UserDetailMixin, TokenObtainPairView):
    permission_classes = [AllowAny]

    # Validate credentials and return login tokens.
    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        if not serializer.is_valid():
            return api_response(False, 'Invalid credentials', {'details': {}}, http_status=401)

        user = serializer.user
        login(request, user)
        return api_response(
            True,
            'Login successful',
            {
                'details': self.get_user_details(user, request),
                'access_token': serializer.validated_data.get('access'),
                'refresh_token': serializer.validated_data.get('refresh'),
            },
            http_status=200,
        )


# Handle JWT refresh requests with the same response format.
class CustomTokenRefreshView(UserDetailMixin, TokenRefreshView):
    permission_classes = [AllowAny]

    # Return a new access token when the refresh token is valid.
    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        if not serializer.is_valid():
            return api_response(False, 'Invalid or expired refresh token', {'details': {}}, http_status=401)

        return api_response(
            True,
            'Token refreshed successfully',
            {'access_token': serializer.validated_data.get('access')},
            http_status=200,
        )