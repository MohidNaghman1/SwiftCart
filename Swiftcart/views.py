from django.views.generic import TemplateView

from orders.models import Order
from memberships.models import MembershipPayment, MembershipPlan, UserMembership
from products.models import Product
from users.models import CustomUser


# Render the login page.
class HomeView(TemplateView):
	template_name = 'login.html'


# Render the registration page.
class RegisterPageView(TemplateView):
	template_name = 'register.html'


# Render the login page.
class LoginPageView(TemplateView):
	template_name = 'login.html'


# Render the customer products page.
class ProductsPageView(TemplateView):
	template_name = 'products.html'


# Render the checkout fallback page.
class CheckoutPageView(TemplateView):
	template_name = 'checkout.html'


# Render the payment success page.
class SuccessPageView(TemplateView):
	template_name = 'success.html'


# Render the payment cancel page.
class CancelPageView(TemplateView):
	template_name = 'cancel.html'


# Render the admin dashboard page with summary counts.
class AdminDashboardView(TemplateView):
	template_name = 'admin/dashboard.html'

	def get_context_data(self, **kwargs):
		context = super().get_context_data(**kwargs)
		context['total_products'] = Product.objects.count()
		context['total_orders'] = Order.objects.count()
		context['total_users'] = CustomUser.objects.count()
		context['total_membership_plans'] = MembershipPlan.objects.count()
		context['active_memberships'] = UserMembership.objects.filter(status='active').count()
		context['pending_memberships'] = UserMembership.objects.filter(status='pending').count()
		context['expired_memberships'] = UserMembership.objects.filter(status='expired').count()
		return context


# Render the admin products page.
class AdminProductsPageView(TemplateView):
	template_name = 'admin/products.html'


# Render the admin categories page.
class AdminCategoriesPageView(TemplateView):
	template_name = 'admin/categories.html'


# Render the admin orders page.
class AdminOrdersPageView(TemplateView):
	template_name = 'admin/orders.html'


# Render the admin users page with a user list.
class AdminUsersPageView(TemplateView):
	template_name = 'admin/users.html'

	def get_context_data(self, **kwargs):
		context = super().get_context_data(**kwargs)
		context['users'] = CustomUser.objects.order_by('-date_joined')
		return context


# Render the admin memberships page with membership, plan, and payment summaries.
class AdminMembershipsPageView(TemplateView):
	template_name = 'admin/memberships.html'

	def get_context_data(self, **kwargs):
		context = super().get_context_data(**kwargs)
		context['plans'] = MembershipPlan.objects.order_by('price')
		context['memberships'] = UserMembership.objects.select_related('user', 'plan').order_by('-updated_at')
		context['payments'] = MembershipPayment.objects.select_related('user', 'membership', 'membership__plan').order_by('-created_at')[:20]
		context['total_plans'] = MembershipPlan.objects.count()
		context['active_memberships'] = UserMembership.objects.filter(status='active').count()
		context['pending_memberships'] = UserMembership.objects.filter(status='pending').count()
		context['expired_memberships'] = UserMembership.objects.filter(status='expired').count()
		context['cancelled_memberships'] = UserMembership.objects.filter(status='cancelled').count()
		return context


