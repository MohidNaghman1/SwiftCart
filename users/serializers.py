from rest_framework import serializers

from Swiftcart.utils import build_absolute_uri
from .models import CustomUser


# Validate and create new user registrations.
class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True)
    password2 = serializers.CharField(write_only=True)
    profile_image = serializers.ImageField(required=False, allow_null=True)

    class Meta:
        model = CustomUser
        fields = ('username', 'email', 'password', 'password2', 'first_name', 'last_name', 'profile_image', 'role')

    # Ensure both password fields match.
    def validate(self, attrs):
        if attrs.get('password') != attrs.get('password2'):
            raise serializers.ValidationError({'password': 'Passwords do not match.'})
        return attrs

    # Return a public representation with an absolute profile image URL.
    def to_representation(self, instance):
        representation = super().to_representation(instance)
        request = self.context.get('request')
        profile_image = instance.profile_image.url if instance.profile_image else None
        representation['profile_image'] = build_absolute_uri(request, profile_image) if request else profile_image
        return representation

    # Create the user with a hashed password.
    def create(self, validated_data):
        validated_data.pop('password2', None)
        password = validated_data.pop('password')
        role = validated_data.pop('role', 'normal_user')
        return CustomUser.objects.create_user(role=role, password=password, **validated_data)