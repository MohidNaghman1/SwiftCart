from Swiftcart.utils import api_response
from rest_framework import status, viewsets
from rest_framework.permissions import IsAuthenticated

from .models import Category, Product
from .permissions import IsAdminUser
from .serializers import CategorySerializer, ProductSerializer


# Share common CRUD permission handling across product viewsets.
class StaffWritePermissionsMixin:
    # Allow authenticated reads and staff-only writes.
    def get_permissions(self):
        if self.action in ['list', 'retrieve']:
            permission_classes = [IsAuthenticated]
        else:
            permission_classes = [IsAuthenticated, IsAdminUser]
        return [permission() for permission in permission_classes]


# Share standardized api_response CRUD actions across product viewsets.
class ApiResponseCrudMixin:
    list_message = ''
    retrieve_message = ''
    create_message = ''
    update_message = ''
    destroy_message = ''

    # Return a standardized list response.
    def list(self, request, *args, **kwargs):
        serializer = self.get_serializer(self.get_queryset(), many=True)
        return api_response(True, self.list_message, {'details': serializer.data}, http_status=status.HTTP_200_OK)

    # Return a standardized retrieve response.
    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(instance)
        return api_response(True, self.retrieve_message, {'details': serializer.data}, http_status=status.HTTP_200_OK)

    # Return a standardized create response.
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        return api_response(True, self.create_message, {'details': serializer.data}, http_status=status.HTTP_201_CREATED)

    # Return a standardized update response.
    def update(self, request, *args, **kwargs):
        partial = kwargs.pop('partial', False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)
        return api_response(True, self.update_message, {'details': serializer.data}, http_status=status.HTTP_200_OK)

    # Return a standardized destroy response.
    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        self.perform_destroy(instance)
        return api_response(True, self.destroy_message, {'details': {}}, http_status=status.HTTP_200_OK)


# Manage category CRUD operations.
class CategoryViewSet(StaffWritePermissionsMixin, ApiResponseCrudMixin, viewsets.ModelViewSet):
    queryset = Category.objects.all()
    serializer_class = CategorySerializer
    list_message = 'Categories retrieved successfully'
    retrieve_message = 'Category retrieved successfully'
    create_message = 'Category created successfully'
    update_message = 'Category updated successfully'
    destroy_message = 'Category deleted successfully'


# Manage product CRUD operations and visibility.
class ProductViewSet(StaffWritePermissionsMixin, ApiResponseCrudMixin, viewsets.ModelViewSet):
    queryset = Product.objects.select_related('category').all()
    serializer_class = ProductSerializer
    list_message = 'Products retrieved successfully'
    retrieve_message = 'Product retrieved successfully'
    create_message = 'Product created successfully'
    update_message = 'Product updated successfully'
    destroy_message = 'Product deleted successfully'

    # Return all products for staff and active products for regular users.
    def get_queryset(self):
        queryset = Product.objects.select_related('category').all()
        if self.request.user.is_authenticated and self.request.user.is_staff:
            return queryset
        return queryset.filter(is_active=True)

    # Pass the current request into serializer context for absolute media URLs.
    def get_serializer_context(self):
        return {'request': self.request}
