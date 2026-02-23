# core/models.py
from random import random
from django.db import models
from django.utils import timezone
from django.utils.text import slugify
from django.core.validators import MinValueValidator
import uuid

from apps.authentication.models import User



# ---------------------------
# Base
# ---------------------------
class BaseModel(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    updated_at = models.DateTimeField(auto_now=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        abstract = True
        ordering = ['-created_at']



class UserSettings(BaseModel):
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name="settings"
    )

    # App
    language = models.CharField(max_length=10, default="en-US")
    currency = models.CharField(max_length=5, default="NGN")

    # Notifications
    email_notifications = models.BooleanField(default=True)
    order_updates = models.BooleanField(default=True)

    # Privacy
    public_profile = models.BooleanField(default=False)
    personalized_recommendations = models.BooleanField(default=True)

    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user.email} settings"
# ---------------------------
# Country & Currency
# ---------------------------
class Country(models.Model):
    name = models.CharField(max_length=100, unique=True)
    code = models.CharField(max_length=5, unique=True)
    continent = models.CharField(max_length=50, blank=True, null=True)

    def __str__(self):
        return self.name


class Currency(models.Model):
    name = models.CharField(max_length=50)
    code = models.CharField(max_length=10, unique=True)
    symbol = models.CharField(max_length=10, blank=True, null=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.code} ({self.symbol})"


# ---------------------------
# Media
# ---------------------------
class MediaAsset(BaseModel):
    class MediaType(models.TextChoices):
        IMAGE = "image"
        VIDEO = "video"
        DOCUMENT = "document"

    file = models.FileField(upload_to="uploads/%Y/%m/%d/")
    media_type = models.CharField(max_length=20, choices=MediaType.choices, default=MediaType.IMAGE)
    alt_text = models.CharField(max_length=255, blank=True, null=True)
    caption = models.TextField(blank=True, null=True)

    def __str__(self):
        return self.alt_text or str(self.file)


# ---------------------------
# Categories
# ---------------------------
class Category(BaseModel):
    name = models.CharField(max_length=100)
    slug = models.SlugField(unique=True, blank = True)
    parent = models.ForeignKey("self", null=True, blank=True, related_name="subcategories", on_delete=models.SET_NULL)
    description = models.TextField(blank=True, null=True)

    def __str__(self):
        return self.name


# ---------------------------
# Products
# ---------------------------
class Brand(models.Model):
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True, null=True)

    def __str__(self):
        return self.name



class Sizes(models.Model):
    name = models.CharField(max_length=50, unique=True)
    description = models.TextField(default=None)

    def __str__(self):
        return self.name
    
class Product(BaseModel):
    user = models.ForeignKey(User, on_delete=models.CASCADE,null = True, default=None, related_name="products")
    name = models.CharField(max_length=200)
    slug = models.SlugField(unique=True, blank=True)
    description = models.TextField()
    price = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(0)])
    discount = models.DecimalField(max_digits=5, decimal_places=2, blank=True, default=0, help_text="Discount in %")
    currency = models.ForeignKey(Currency, on_delete=models.SET_NULL, null=True)
    category = models.ForeignKey(Category, related_name='products', on_delete=models.SET_NULL, null=True)
    subcategory = models.ForeignKey(Category, related_name='sub_products', on_delete=models.SET_NULL, null=True, blank=True)
    brand = models.ForeignKey(Brand, on_delete=models.SET_NULL, null=True, blank=True)
    material = models.CharField(max_length=100, blank=True, null=True)
    origin = models.CharField(max_length=100, blank=True, null=True)
    sizes = models.ManyToManyField(Sizes, default=None, blank=True,)
    stock = models.PositiveIntegerField(default=0)
    sku = models.CharField(max_length=50, unique=True, blank=True, null=True)
    is_published = models.BooleanField(default=False)
    is_admin_unpublished = models.BooleanField(default=False)
    media = models.ManyToManyField(MediaAsset, blank=True, related_name='products')
    featured = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    stock = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    is_sustainable = models.BooleanField(default=False)  # for sustainable tab
    
    # Popularity metric for trending tab
    popularity_score = models.PositiveIntegerField(default=0)
    
    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name + str(round(random()*9999999)))
        if not self.sku:
            self.sku = f"PROD-{uuid.uuid4().hex[:8].upper()}"
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class Color(models.Model):
    product = models.ForeignKey(Product, null= True, default=None, related_name='colors', on_delete=models.CASCADE)
    name = models.CharField(max_length=50, unique=True)
    hex_code = models.CharField(max_length=7, blank=True, null=True)  # optional

    def __str__(self):
        return self.name
# ---------------------------
# Reviews
# ---------------------------
class Review(BaseModel):
    product = models.ForeignKey(Product, related_name='reviews', on_delete=models.CASCADE)
    customer = models.ForeignKey('customers.Customer', related_name='reviews', on_delete=models.CASCADE)
    rating = models.PositiveSmallIntegerField(validators=[MinValueValidator(1)], default=5)
    comment = models.TextField(blank=True, null=True)
    is_approved = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.rating} stars by {self.customer}"


class ShippingMethod(models.Model):
    """Available shipping methods for orders."""
    name = models.CharField(max_length=100)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    estimated_days = models.PositiveIntegerField(help_text="Estimated delivery in days")
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.name} (${self.price})"






class ContactMessage(models.Model):
    name = models.CharField(max_length=150)
    email = models.EmailField()
    message = models.TextField()

    user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="contact_messages",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    is_resolved = models.BooleanField(default=False)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.name} - {self.email}"
