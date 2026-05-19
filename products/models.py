from django.db import models


# Store a simple product category.
class Category(models.Model):
	name = models.CharField(max_length=100, unique=True)
	description = models.TextField(blank=True)
	created_at = models.DateTimeField(auto_now_add=True)

	class Meta:
		ordering = ('name',)

	# Return the category name in admin and shell.
	def __str__(self):
		return self.name


# Store a product that belongs to a category.
class Product(models.Model):
	name = models.CharField(max_length=255)
	description = models.TextField()
	price = models.DecimalField(max_digits=10, decimal_places=2)
	stock = models.PositiveIntegerField()
	is_active = models.BooleanField(default=True)
	image = models.ImageField(upload_to='products/images/', null=True, blank=True)
	category = models.ForeignKey(Category, on_delete=models.SET_NULL, null=True, blank=True, related_name='products')
	created_at = models.DateTimeField(auto_now_add=True)
	updated_at = models.DateTimeField(auto_now=True)

	class Meta:
		ordering = ('-created_at',)

	# Return the product name in admin and shell.
	def __str__(self):
		return self.name
