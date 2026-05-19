from django.contrib.auth.base_user import BaseUserManager
from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.db import models


# Manage creation of custom users and superusers.
class CustomUserManager(BaseUserManager):
	use_in_migrations = True

	# Create a regular user.
	def create_user(self, username, email, password=None, **extra_fields):
		if not username:
			raise ValueError('The username field must be set.')
		if not email:
			raise ValueError('The email field must be set.')

		email = self.normalize_email(email)
		user = self.model(username=username, email=email, **extra_fields)
		user.set_password(password)
		user.save(using=self._db)
		return user

	# Create a superuser with admin privileges.
	def create_superuser(self, username, email, password=None, **extra_fields):
		extra_fields.setdefault('is_staff', True)
		extra_fields.setdefault('is_superuser', True)
		extra_fields.setdefault('role', 'admin')

		if extra_fields.get('is_staff') is not True:
			raise ValueError('Superuser must have is_staff=True.')
		if extra_fields.get('is_superuser') is not True:
			raise ValueError('Superuser must have is_superuser=True.')

		return self.create_user(username, email, password, **extra_fields)


# Store the project's custom authentication user.
class CustomUser(AbstractBaseUser, PermissionsMixin):
	ROLE_CHOICES = [
		('admin', 'Admin'),
		('normal_user', 'Normal User'),
	]

	username = models.CharField(max_length=150, unique=True)
	email = models.CharField(max_length=255, unique=True)
	first_name = models.CharField(max_length=100, blank=True)
	last_name = models.CharField(max_length=100, blank=True)
	profile_image = models.ImageField(upload_to='users/profile_images/', null=True, blank=True)
	role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='normal_user')
	is_active = models.BooleanField(default=True)
	is_staff = models.BooleanField(default=False)
	date_joined = models.DateTimeField(auto_now_add=True)

	objects = CustomUserManager()

	USERNAME_FIELD = 'username'
	REQUIRED_FIELDS = ['email']

	class Meta:
		verbose_name = 'user'
		verbose_name_plural = 'users'

	# Return the username in the admin and shell.
	def __str__(self):
		return self.username
