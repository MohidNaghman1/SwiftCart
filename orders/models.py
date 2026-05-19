from decimal import Decimal

from django.conf import settings
from django.db import models


class Order(models.Model):
	STATUS_PENDING = 'pending'
	STATUS_PROCESSING = 'processing'
	STATUS_PAID = 'paid'
	STATUS_CANCELLED = 'cancelled'
	STATUS_DELIVER = 'deliver'
	STATUS_DELIVERED = 'delivered'
	STATUS_DELIVERD = 'deliverd'

	STATUS_CHOICES = (
		(STATUS_PENDING, 'Pending'),
		(STATUS_PROCESSING, 'Processing'),
		(STATUS_PAID, 'Paid'),
		(STATUS_CANCELLED, 'Cancelled'),
		(STATUS_DELIVER, 'Deliver'),
		(STATUS_DELIVERED, 'Delivered'),
		(STATUS_DELIVERD, 'Deliverd'),
	)

	user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='orders')
	created_at = models.DateTimeField(auto_now_add=True)
	status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
	stripe_session_id = models.CharField(max_length=255, blank=True, null=True)
	payment_method = models.CharField(max_length=50, blank=True, null=True)

	class Meta:
		ordering = ('-created_at',)

	def __str__(self):
		return f'Order #{self.pk} - {self.user}'

	@property
	def total_amount(self):
		return sum((item.total_price for item in self.items.all()), Decimal('0.00'))


class OrderItem(models.Model):
	order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='items')
	product = models.ForeignKey('products.Product', on_delete=models.CASCADE)
	quantity = models.PositiveIntegerField()
	price_at_purchase = models.DecimalField(max_digits=10, decimal_places=2)

	def __str__(self):
		return f'{self.product} x {self.quantity}'

	@property
	def total_price(self):
		return self.quantity * self.price_at_purchase
