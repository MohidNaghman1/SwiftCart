from rest_framework import serializers
from django.db import transaction

from products.models import Product

from .models import Order, OrderItem


class OrderItemSerializer(serializers.ModelSerializer):
    product = serializers.PrimaryKeyRelatedField(queryset=Product.objects.filter(is_active=True))

    class Meta:
        model = OrderItem
        fields = ('product', 'quantity', 'price_at_purchase')
        read_only_fields = ('price_at_purchase',)


class OrderSerializer(serializers.ModelSerializer):
    user = serializers.CharField(source='user.username', read_only=True)
    items = OrderItemSerializer(many=True)
    total_amount = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)

    class Meta:
        model = Order
        fields = ('id', 'user', 'status', 'created_at', 'items', 'total_amount', 'payment_method')
        read_only_fields = ('id', 'user', 'created_at', 'total_amount', 'payment_method')

    def create(self, validated_data):
        items_data = validated_data.pop('items')

        with transaction.atomic():
            order = Order.objects.create(**validated_data)

            for item_data in items_data:
                product = item_data['product']
                quantity = item_data['quantity']

                if product.stock < quantity:
                    order.delete()
                    raise serializers.ValidationError(
                        f'Not enough stock for "{product.name}". Available: {product.stock}'
                    )

                product.stock -= quantity
                product.save(update_fields=['stock'])

                OrderItem.objects.create(
                    order=order,
                    product=product,
                    quantity=quantity,
                    price_at_purchase=product.price,
                )

            return order