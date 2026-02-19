from django.db import models
from django.contrib.auth import get_user_model
from django.utils.text import slugify
from apps.core.models import BaseModel, Product
from django.utils import timezone

from apps.customers.models import OrderItem
User = get_user_model()

# -------------------------------
# Designer Profile
# -------------------------------
class Designer(BaseModel):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='designer_profile')
    bio = models.TextField(blank=True)
    story = models.TextField(blank=True)
    brand_name = models.CharField(max_length=200,default='',blank=True)
    specialty = models.CharField(max_length=200,default='',blank=True)
    country = models.TextField(blank=True)
    years_of_experience = models.IntegerField(blank=True, default = 0)
    profile_picture = models.ImageField(upload_to='designer_profiles/', blank=True, null=True)
    banner_image = models.ImageField(upload_to='designer_profiles/', blank=True, null=True)
    website = models.URLField(blank=True)
    instagram = models.URLField(blank=True)
    is_verified = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    slug = models.SlugField(unique=True, blank=True, null=True)

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(f"{self.brand_name}")
        super().save(*args, **kwargs)

    def __str__(self):
        return self.user.username

# -------------------------------
# Collections
# -------------------------------
class Collection(BaseModel):
    designer = models.ForeignKey(Designer, on_delete=models.CASCADE, related_name='collections')
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    cover_image = models.ImageField(upload_to='designer_collections/', blank=True, null=True)
    is_published = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    slug = models.SlugField(unique=True, blank=True, null=True)

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(f"{self.title}-{self.id}")
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.designer.user.username} - {self.title}"

# -------------------------------
# Designer Products
# -------------------------------
class DesignerProduct(BaseModel):
    designer = models.ForeignKey(Designer, on_delete=models.CASCADE, related_name='products')
    product = models.OneToOneField(Product, on_delete=models.CASCADE, related_name='designer_product')
    featured = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    stock = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.product.name} by {self.designer.user.username}"

# -------------------------------
# Product Images
# -------------------------------
class ProductImage(BaseModel):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='images')
    image = models.ImageField(upload_to='product_images/')
    alt_text = models.CharField(max_length=255, blank=True)
    is_featured = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.product.name} Image"

# -------------------------------
# Shipping Options
# -------------------------------
class ShippingOption(BaseModel):
    designer = models.ForeignKey(Designer, on_delete=models.CASCADE, related_name='shipping_options')
    name = models.CharField(max_length=100)  # e.g., "Standard", "Express"
    cost = models.DecimalField(max_digits=10, decimal_places=2)
    estimated_days = models.IntegerField()
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.name} ({self.designer.user.username})"

# -------------------------------
# Orders (designer view)
# -------------------------------
class DesignerOrder(BaseModel):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('processing', 'Processing'),
        ('shipped', 'Shipped'),
        ('delivered', 'Delivered'),
        ('cancelled', 'Cancelled'),
    ]
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='orders')
    order_item = models.ForeignKey(OrderItem, default=None, on_delete=models.CASCADE)
    shipping_option = models.ForeignKey(ShippingOption, on_delete=models.SET_NULL, null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Order #{self.id} by {self.user.first_name}"

# -------------------------------
# Analytics / Stats
# -------------------------------
class DesignerAnalytics(BaseModel):
    designer = models.OneToOneField(Designer, on_delete=models.CASCADE, related_name='analytics')
    total_sales = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_orders = models.PositiveIntegerField(default=0)
    total_products = models.PositiveIntegerField(default=0)
    last_updated = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Analytics for {self.designer.user.username}"



# -------------------------------
# Inventory Alert
# -------------------------------
class InventoryAlert(BaseModel):
    designer_product = models.OneToOneField('DesignerProduct', on_delete=models.CASCADE, related_name='inventory_alert')
    threshold = models.PositiveIntegerField(default=5)
    notified = models.BooleanField(default=False)

    def __str__(self):
        return f"Alert for {self.designer_product.product.name}"

# -------------------------------
# Promotions / Discounts
# -------------------------------
class Promotion(BaseModel):
    designer = models.ForeignKey('Designer', on_delete=models.CASCADE, related_name='promotions')
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    discount_percentage = models.DecimalField(max_digits=5, decimal_places=2)
    active = models.BooleanField(default=True)
    start_date = models.DateTimeField()
    end_date = models.DateTimeField()

    def __str__(self):
        return f"{self.title} ({self.designer.user.username})"

# -------------------------------
# Shipment Tracking
# -------------------------------
class ShipmentTracking(BaseModel):
    order = models.OneToOneField('DesignerOrder', on_delete=models.CASCADE, related_name='shipment')
    tracking_number = models.CharField(max_length=100, blank=True, null=True)
    carrier = models.CharField(max_length=100, blank=True, null=True)
    status = models.CharField(max_length=50, default='pending')  # pending, in_transit, delivered
    last_updated = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Shipment for Order #{self.order.id}"

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


class DesignerStory(BaseModel):
    designer = models.ForeignKey('Designer', on_delete=models.CASCADE, related_name='stories')
    title = models.CharField(max_length=255, blank=True)
    media = models.FileField(upload_to='designer_stories/')  # image/video
    caption = models.TextField(blank=True)
    start_time = models.DateTimeField(default=timezone.now)
    end_time = models.DateTimeField()
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def has_expired(self):
        return timezone.now() > self.end_time

    def __str__(self):
        return f"{self.designer.user.username} - {self.title or 'Story'}"

class StoryView(BaseModel):
    story = models.ForeignKey(DesignerStory, on_delete=models.CASCADE, related_name='views')
    viewer = models.ForeignKey('customers.Customer', on_delete=models.CASCADE)  # assuming a Customer model
    viewed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('story', 'viewer')  # each user counts once per story

    def __str__(self):
        return f"{self.viewer.user.username} viewed {self.story}"