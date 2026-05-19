from rest_framework import serializers

from Swiftcart.utils import build_absolute_uri
from .models import Category, Product


# Serialize product categories.
class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = '__all__'


# Serialize products without exposing writable timestamps.
class ProductSerializer(serializers.ModelSerializer):
    image = serializers.ImageField(required=False, allow_null=True)

    class Meta:
        model = Product
        fields = '__all__'
        read_only_fields = ('id', 'created_at', 'updated_at')

    # Return a public representation with an absolute image URL.
    def to_representation(self, instance):
        rep = super().to_representation(instance)
        request = self.context.get('request')
        rep['image'] = build_absolute_uri(request, instance.image.url if instance.image else None)
        return rep