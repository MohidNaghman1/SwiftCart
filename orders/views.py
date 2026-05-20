import json
import logging
from decimal import Decimal

import stripe
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from rest_framework import mixins, permissions, status, viewsets
from rest_framework.exceptions import ValidationError
from rest_framework.views import APIView

from Swiftcart.utils import api_response, build_absolute_uri
from products.models import Product

from .models import Order, OrderItem
from .permissions import IsAdminUser
from .serializers import OrderSerializer


logger = logging.getLogger(__name__)

# Keep Stripe configured from project settings.
stripe.api_key = settings.STRIPE_SECRET_KEY

User = get_user_model()


def _metadata_value(metadata, key, default=None):
	if not metadata:
		return default
	if isinstance(metadata, dict):
		return metadata.get(key, default)
	return getattr(metadata, key, default)


def _is_membership_checkout(data_object):
	metadata = getattr(data_object, 'metadata', None)
	return _metadata_value(metadata, 'purpose') == 'membership' or getattr(data_object, 'mode', None) == 'subscription'


# Manage order listing, retrieval, and admin updates only.
class OrderViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.UpdateModelMixin, viewsets.GenericViewSet):
	queryset = Order.objects.select_related('user').prefetch_related('items__product').all()
	serializer_class = OrderSerializer

	# Allow authenticated reads and staff-only updates.
	def get_permissions(self):
		if self.action in ('update', 'partial_update'):
			return [IsAdminUser()]
		return [permissions.IsAuthenticated()]

	# Return all orders for staff and user-owned orders for regular users.
	def get_queryset(self):
		queryset = Order.objects.select_related('user').prefetch_related('items__product').all()
		user = self.request.user
		if user.is_authenticated and user.is_staff:
			return queryset
		if user.is_authenticated:
			return queryset.filter(user=user, items__isnull=False).distinct()
		return queryset.none()

	# Return standardized order lists.
	def list(self, request, *args, **kwargs):
		logger.info(f"OrderViewSet.list: Listing orders for user {request.user.username} (IsStaff: {request.user.is_staff})")
		serializer = self.get_serializer(self.get_queryset(), many=True)
		return api_response(True, 'Orders retrieved successfully', {'details': serializer.data}, http_status=status.HTTP_200_OK)

	# Return standardized order details.
	def retrieve(self, request, *args, **kwargs):
		instance = self.get_object()
		logger.info(f"OrderViewSet.retrieve: Retrieving details for Order {instance.id} (requested by {request.user.username})")
		serializer = self.get_serializer(instance)
		return api_response(True, 'Order retrieved successfully', {'details': serializer.data}, http_status=status.HTTP_200_OK)

	# Allow staff to update status and restore stock on cancellation.
	def update(self, request, *args, **kwargs):
		instance = self.get_object()
		new_status = request.data.get('status')
		logger.info(f"OrderViewSet.update: Admin {request.user.username} updating Order {instance.id} from status {instance.status} to {new_status}")

		if new_status == 'cancelled' and instance.status != 'cancelled':
			with transaction.atomic():
				for item in instance.items.select_related('product').all():
					logger.info(f"OrderViewSet.update: Restoring stock for product {item.product.name} (ID: {item.product.id}). Old stock: {item.product.stock}, Restoring: {item.quantity}")
					item.product.stock += item.quantity
					item.product.save(update_fields=['stock'])

				instance.status = 'cancelled'
				instance.save(update_fields=['status'])
				logger.info(f"OrderViewSet.update: Order {instance.id} status updated to cancelled.")

			return api_response(
				True,
				'Order cancelled and stock restored',
				{'details': OrderSerializer(instance).data},
				http_status=status.HTTP_200_OK,
			)

		partial = kwargs.pop('partial', False)
		serializer = self.get_serializer(instance, data=request.data, partial=partial)
		serializer.is_valid(raise_exception=True)
		self.perform_update(serializer)
		logger.info(f"OrderViewSet.update: Order {instance.id} update completed. New status: {instance.status}")
		return api_response(True, 'Order updated successfully', {'details': serializer.data}, http_status=status.HTTP_200_OK)


# Initialize a Stripe checkout session without storing an order yet.
class StripeCheckoutInitView(APIView):
	permission_classes = [permissions.IsAuthenticated]

	# Validate cart items, check stock, and return a Stripe checkout URL.
	def post(self, request, *args, **kwargs):
		logger.info(f"StripeCheckoutInitView: Creating checkout session for user: {request.user.username} (ID: {request.user.id})")
		items = request.data.get('items', [])
		if not isinstance(items, list) or not items:
			logger.warning(f"StripeCheckoutInitView: Invalid items format or empty items list for user {request.user.username}")
			raise ValidationError({'items': 'At least one item is required.'})

		validated_items = []
		line_items = []

		for item in items:
			product_id = item.get('product')
			quantity = item.get('quantity')

			if product_id is None or quantity is None:
				logger.warning(f"StripeCheckoutInitView: Missing product or quantity in item data: {item}")
				raise ValidationError({'items': 'Each item must include product and quantity.'})

			try:
				quantity = int(quantity)
			except (TypeError, ValueError):
				logger.warning(f"StripeCheckoutInitView: Invalid quantity format: {quantity}")
				raise ValidationError({'items': 'Quantity must be a valid integer.'})

			if quantity <= 0:
				logger.warning(f"StripeCheckoutInitView: Non-positive quantity: {quantity}")
				raise ValidationError({'items': 'Quantity must be greater than zero.'})

			try:
				product = Product.objects.get(pk=product_id, is_active=True)
			except Product.DoesNotExist:
				logger.warning(f"StripeCheckoutInitView: Product {product_id} not found or inactive")
				raise ValidationError({'items': f'Product {product_id} was not found.'})

			if product.stock < quantity:
				logger.warning(f"StripeCheckoutInitView: Insufficient stock for product {product.name} (stock: {product.stock}, requested: {quantity})")
				raise ValidationError({'items': f'Not enough stock for "{product.name}". Available: {product.stock}'})

			logger.info(f"StripeCheckoutInitView: Validated item {product.name} (ID: {product.id}), Price: {product.price}, Qty: {quantity}")
			validated_items.append({
				'product_id': product.id,
				'quantity': quantity,
				'price': str(product.price),
			})
			line_items.append(
				{
					'price_data': {
						'currency': 'usd',
						'product_data': {'name': product.name},
						'unit_amount': int(product.price * 100),
					},
					'quantity': quantity,
				}
			)

		metadata = {
			'user_id': str(request.user.id),
			'items': json.dumps(validated_items),
		}

		logger.info(f"StripeCheckoutInitView: Initializing Stripe Session with metadata: {metadata}")
		session_kwargs = {
			'payment_method_types': ['card'],
			'line_items': line_items,
			'mode': 'payment',
			'success_url': build_absolute_uri(request, '/success/') + '?session_id={CHECKOUT_SESSION_ID}',
			'cancel_url': build_absolute_uri(request, '/cancel/'),
			'metadata': metadata,
			'payment_intent_data': {
				'metadata': metadata,
			},
		}
		if request.user.email:
			session_kwargs['customer_email'] = request.user.email

		session = stripe.checkout.Session.create(**session_kwargs)

		logger.info(f"StripeCheckoutInitView: Stripe Session created successfully. ID: {session.id}, URL: {session.url}")
		return api_response(True, 'Checkout session created successfully', {'checkout_url': session.url}, http_status=status.HTTP_200_OK)


# Finalize payment and create the order only after Stripe confirms success.
class StripeSuccessView(APIView):
	permission_classes = [permissions.AllowAny]

	# Convert a paid Stripe session into a stored order.
	def get(self, request, *args, **kwargs):
		session_id = request.query_params.get('session_id') or (request.data.get('session_id') if isinstance(request.data, dict) else None)
		logger.info(f"StripeSuccessView: received request for session_id: {session_id}")
		if not session_id:
			logger.warning("StripeSuccessView: missing session_id in request.")
			raise ValidationError({'session_id': 'session_id is required.'})

		session = stripe.checkout.Session.retrieve(session_id)
		payment_method_type = 'card'
		if session.payment_intent:
			try:
				payment_intent = stripe.PaymentIntent.retrieve(session.payment_intent)
				payment_method_details = stripe.PaymentMethod.retrieve(payment_intent.payment_method)
				payment_method_type = payment_method_details.type  # e.g. 'card'

				# If it's a card payment, check if it was made via a digital wallet (like google_pay or apple_pay)
				if payment_method_type == 'card':
					card_details = getattr(payment_method_details, 'card', None)
					if card_details:
						wallet_details = getattr(card_details, 'wallet', None)
						if wallet_details:
							payment_method_type = getattr(wallet_details, 'type', 'card')
			except Exception as e:
				logger.error(f"StripeSuccessView: error fetching payment method details: {str(e)}")

		logger.info(f"StripeSuccessView: identified payment method type: {payment_method_type}")

		existing_order = Order.objects.prefetch_related('items__product').filter(stripe_session_id=session_id).first()
		if existing_order:
			logger.info(f"StripeSuccessView: existing order found for session {session_id} (ID: {existing_order.id}, Status: {existing_order.status}).")
			return api_response(
				True,
				'Payment successful. Order already created',
				{'details': OrderSerializer(existing_order).data},
				http_status=status.HTTP_200_OK,
			)

		user_id = session.metadata['user_id'] if session.metadata else None
		items_raw = session.metadata['items'] if session.metadata else '[]'
		logger.info(f"StripeSuccessView: metadata user_id: {user_id}")

		if not user_id:
			logger.error("StripeSuccessView: user_id missing from Stripe session metadata.")
			raise ValidationError({'user_id': 'Missing user_id in Stripe metadata.'})

		try:
			items_data = json.loads(items_raw)
		except json.JSONDecodeError:
			logger.error("StripeSuccessView: failed to parse items metadata JSON.")
			raise ValidationError({'items': 'Invalid items metadata.'})

		if not isinstance(items_data, list) or not items_data:
			logger.error("StripeSuccessView: items list is empty or invalid.")
			raise ValidationError({'items': 'No items were found in Stripe metadata.'})

		try:
			user = User.objects.get(pk=user_id)
		except User.DoesNotExist:
			logger.error(f"StripeSuccessView: user {user_id} does not exist.")
			raise ValidationError({'user_id': 'User not found for this session.'})

		with transaction.atomic():
			order = Order.objects.create(
				user=user,
				status=Order.STATUS_PROCESSING,
				stripe_session_id=session_id,
				payment_method=payment_method_type,
			)
			logger.info(f"StripeSuccessView: Created order {order.id} in PROCESSING status.")

			for item_data in items_data:
				product_id = item_data.get('product_id')
				quantity = item_data.get('quantity')
				price = item_data.get('price')

				if product_id is None or quantity is None or price is None:
					raise ValidationError({'items': 'Invalid item metadata received from Stripe.'})

				try:
					quantity = int(quantity)
				except (TypeError, ValueError):
					raise ValidationError({'items': 'Invalid item quantity received from Stripe.'})

				try:
					product = Product.objects.get(pk=product_id, is_active=True)
				except Product.DoesNotExist:
					raise ValidationError({'items': f'Product {product_id} was not found.'})

				# Create OrderItem directly without stock checks or deduction
				OrderItem.objects.create(
					order=order,
					product=product,
					quantity=quantity,
					price_at_purchase=Decimal(price),
				)
				logger.info(f"StripeSuccessView: Added product {product.id} ({product.name}) x {quantity} to order {order.id}.")

		logger.info(f"StripeSuccessView: successfully completed order creation for session {session_id} (Order ID: {order.id}).")
		return api_response(True, 'Payment processing. Order created.', {'details': OrderSerializer(order).data}, http_status=status.HTTP_200_OK)

	def post(self, request, *args, **kwargs):
		return self.get(request, *args, **kwargs)


# Return a simple cancel response because nothing is stored before payment.
class StripeCancelView(APIView):
	permission_classes = [permissions.AllowAny]

	# A cancelled checkout leaves the database unchanged.
	def get(self, request, *args, **kwargs):
		session_id = request.query_params.get('session_id')
		logger.info(f"StripeCancelView: Checkout cancelled by user. Session ID: {session_id}")
		return api_response(True, 'Payment cancelled. Nothing was stored.', {'details': {}}, http_status=status.HTTP_200_OK)


@method_decorator(csrf_exempt, name='dispatch')
class StripeWebhookView(APIView):
	permission_classes = [permissions.AllowAny]

	def post(self, request, *args, **kwargs):
		payload = request.body
		sig_header = request.META.get('HTTP_STRIPE_SIGNATURE')
		endpoint_secret = settings.STRIPE_WEBHOOK_SECRET

		logger.info(f"StripeWebhookView: Received webhook request. Signature: {sig_header}")

		try:
			event = stripe.Webhook.construct_event(
				payload, sig_header, endpoint_secret
			)
			logger.info(f"StripeWebhookView: Successfully constructed event of type: {event['type']}")
		except ValueError as e:
			logger.error(f"StripeWebhookView: Invalid payload: {str(e)}")
			return api_response(False, "Invalid payload", http_status=status.HTTP_400_BAD_REQUEST)
		except stripe.error.SignatureVerificationError as e:
			logger.error(f"StripeWebhookView: Invalid signature: {str(e)}")
			return api_response(False, "Invalid signature", http_status=status.HTTP_400_BAD_REQUEST)
		except Exception as e:
			logger.error(f"StripeWebhookView: Construct event exception: {str(e)}")
			return api_response(False, f"Webhook error: {str(e)}", http_status=status.HTTP_400_BAD_REQUEST)

		try:
			event_type = event['type']
			data_object = event['data']['object']
			logger.info(f"StripeWebhookView: Processing event_type: {event_type}")

			if event_type == 'checkout.session.completed':
				session_id = data_object['id']
				order = Order.objects.filter(stripe_session_id=session_id).first()
				payment_status = getattr(data_object, 'payment_status', None)
				logger.info(f"StripeWebhookView: checkout.session.completed. session_id: {session_id}, payment_status: {payment_status}, order_found: {order is not None}")

				if _is_membership_checkout(data_object):
					logger.info(f"StripeWebhookView: Ignoring membership checkout session {session_id} in order webhook.")
					return api_response(True, "Membership checkout ignored by order webhook", http_status=status.HTTP_200_OK)

				if payment_status == 'paid':
					if order:
						logger.info(f"StripeWebhookView: Order found. Current status: {order.status}")
						if order.status == Order.STATUS_PROCESSING:
							with transaction.atomic():
								for item in order.items.select_related('product').all():
									product = Product.objects.select_for_update().get(pk=item.product.pk)
									logger.info(f"StripeWebhookView: Deducting stock for {product.name}. Old stock: {product.stock}, Quantity: {item.quantity}")
									product.stock -= item.quantity
									product.save(update_fields=['stock'])
								order.status = Order.STATUS_PENDING
								order.save(update_fields=['status'])
								logger.info(f"StripeWebhookView: Order {order.id} status updated to PENDING (from processing).")
						elif order.status == Order.STATUS_PENDING:
							logger.info(f"StripeWebhookView: Order {order.id} is already in PENDING state.")
					else:
						# Fallback safety: order doesn't exist yet (webhook arrived before success redirect)
						metadata = getattr(data_object, 'metadata', None)
						user_id = _metadata_value(metadata, 'user_id')
						items_raw = _metadata_value(metadata, 'items', '[]')
						logger.info(f"StripeWebhookView: Fallback trigger. user_id: {user_id}")
						if user_id:
							try:
								user = User.objects.get(pk=user_id)
								items_data = json.loads(items_raw)
								if not isinstance(items_data, list) or not items_data:
									logger.info(f"StripeWebhookView: No order items present for session {session_id}; skipping fallback order creation.")
									return api_response(True, "No order items to create", http_status=status.HTTP_200_OK)
								
								payment_intent_id = getattr(data_object, 'payment_intent', None)
								payment_method_type = 'card'
								if payment_intent_id:
									try:
										payment_intent = stripe.PaymentIntent.retrieve(payment_intent_id)
										payment_method_details = stripe.PaymentMethod.retrieve(payment_intent.payment_method)
										payment_method_type = payment_method_details.type
										if payment_method_type == 'card':
											card_details = getattr(payment_method_details, 'card', None)
											if card_details:
												wallet_details = getattr(card_details, 'wallet', None)
												if wallet_details:
													payment_method_type = getattr(wallet_details, 'type', 'card')
									except Exception as e:
										logger.error(f"StripeWebhookView: Fallback payment method retrieval failed: {str(e)}")

								with transaction.atomic():
									order = Order.objects.create(
										user=user,
										status=Order.STATUS_PENDING,
										stripe_session_id=session_id,
										payment_method=payment_method_type,
									)
									logger.info(f"StripeWebhookView: Fallback created order {order.id} as PENDING.")
									for item_data in items_data:
										product_id = item_data.get('product_id')
										quantity = int(item_data.get('quantity'))
										price = item_data.get('price')

										product = Product.objects.select_for_update().get(pk=product_id)
										logger.info(f"StripeWebhookView: Fallback deducting stock for {product.name}. Old stock: {product.stock}, Quantity: {quantity}")
										product.stock -= quantity
										product.save(update_fields=['stock'])

										OrderItem.objects.create(
											order=order,
											product=product,
											quantity=quantity,
											price_at_purchase=Decimal(price),
										)
							except Exception as e:
								logger.error(f"StripeWebhookView: Fallback execution exception: {str(e)}")
				elif payment_status == 'unpaid':
					logger.info(f"StripeWebhookView: Payment status is unpaid. Order exists: {order is not None}")
					if order:
						if order.status == Order.STATUS_PROCESSING:
							order.status = Order.STATUS_PENDING
							order.save(update_fields=['status'])
							logger.info(f"StripeWebhookView: Updated order {order.id} status to PENDING.")
					else:
						# Fallback safety: create pending
						metadata = getattr(data_object, 'metadata', None)
						user_id = _metadata_value(metadata, 'user_id')
						items_raw = _metadata_value(metadata, 'items', '[]')
						logger.info(f"StripeWebhookView: Fallback unpaid order creation. user_id: {user_id}")
						if user_id:
							try:
								user = User.objects.get(pk=user_id)
								items_data = json.loads(items_raw)
								if not isinstance(items_data, list) or not items_data:
									logger.info(f"StripeWebhookView: No order items present for session {session_id}; skipping fallback unpaid order creation.")
									return api_response(True, "No order items to create", http_status=status.HTTP_200_OK)
								with transaction.atomic():
									order = Order.objects.create(
										user=user,
										status=Order.STATUS_PENDING,
										stripe_session_id=session_id,
									)
									logger.info(f"StripeWebhookView: Fallback created order {order.id} as PENDING.")
									for item_data in items_data:
										product_id = item_data.get('product_id')
										quantity = int(item_data.get('quantity'))
										price = item_data.get('price')
										product = Product.objects.get(pk=product_id)
										OrderItem.objects.create(
											order=order,
											product=product,
											quantity=quantity,
											price_at_purchase=Decimal(price),
										)
							except Exception as e:
								logger.error(f"StripeWebhookView: Fallback unpaid creation failed: {str(e)}")

			elif event_type == 'checkout.session.expired':
				session_id = data_object['id']
				order = Order.objects.filter(stripe_session_id=session_id).first()
				logger.info(f"StripeWebhookView: checkout.session.expired. session_id: {session_id}, order_found: {order is not None}")
				if order and order.status != Order.STATUS_CANCELLED:
					with transaction.atomic():
						if order.status == Order.STATUS_PAID:
							for item in order.items.select_related('product').all():
								product = Product.objects.select_for_update().get(pk=item.product.pk)
								logger.info(f"StripeWebhookView: Restoring stock for {product.name}. Old stock: {product.stock}, Quantity: {item.quantity}")
								product.stock += item.quantity
								product.save(update_fields=['stock'])
						order.status = Order.STATUS_CANCELLED
						order.save(update_fields=['status'])
						logger.info(f"StripeWebhookView: Order {order.id} status updated to CANCELLED.")

			elif event_type == 'payment_intent.payment_failed':
				payment_intent_id = data_object['id']
				logger.info(f"StripeWebhookView: payment_intent.payment_failed. intent: {payment_intent_id}")
				sessions = stripe.checkout.Session.list(payment_intent=payment_intent_id, limit=1)
				if sessions.data:
					session_id = sessions.data[0].id
					order = Order.objects.filter(stripe_session_id=session_id).first()
					if order:
						order.status = Order.STATUS_CANCELLED
						order.save(update_fields=['status'])

			return api_response(True, "Webhook processed successfully", http_status=status.HTTP_200_OK)

		except Exception as e:
			return api_response(True, f"Error processing webhook: {str(e)}", http_status=status.HTTP_200_OK)


class OrderStatusPollView(APIView):
	permission_classes = [permissions.AllowAny]

	def get(self, request, session_id, *args, **kwargs):
		logger.info(f"OrderStatusPollView: Polling status for session_id: {session_id}")
		order = Order.objects.filter(stripe_session_id=session_id).first()
		if not order:
			logger.info(f"OrderStatusPollView: Order not found in database for session {session_id} yet. Returning PROCESSING.")
			return api_response(True, 'Order not found yet, assuming processing', {'status': Order.STATUS_PROCESSING}, http_status=status.HTTP_200_OK)
		logger.info(f"OrderStatusPollView: Order found for session {session_id} (ID: {order.id}). Status is: {order.status}")
		return api_response(True, 'Order status fetched', {'status': order.status, 'order_id': order.id}, http_status=status.HTTP_200_OK)