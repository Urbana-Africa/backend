from django.db import models
from django.core.exceptions import ObjectDoesNotExist
from django.conf import settings
from django.utils.text import slugify
from django.core.validators import MinValueValidator
from apps.core.models import Color, Product, Sizes
from apps.pay.models import Invoice
from apps.utils.uuid_generator import generate_random_numbers


class Customer(models.Model):
    """Customer profile linked to the user account."""
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='customer_profile')
    phone = models.CharField(max_length=20, blank=True, null=True)
    avatar = models.ImageField(upload_to='customers/avatars/', blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.user.get_full_name() or self.user.username


class Address(models.Model):
    """Shipping/Billing address."""
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name='addresses')
    label = models.CharField(max_length=50, default="Home")
    line1 = models.CharField(max_length=255)
    line2 = models.CharField(max_length=255, blank=True, null=True)
    city = models.CharField(max_length=100)
    state = models.CharField(max_length=100)
    country = models.CharField(max_length=100)
    postal_code = models.CharField(max_length=20)
    is_default = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.label} - {self.customer}"


class Wishlist(models.Model):
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name='wishlist')
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='wishlisted_by')
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('customer', 'product')


class CartItem(models.Model):
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name='cart_items')
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    color = models.ForeignKey(Color, on_delete=models.SET_NULL, null=True, default = None)
    size = models.ForeignKey(Sizes, on_delete=models.SET_NULL, null=True,default=None,)
    quantity = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1)])
    added_at = models.DateTimeField(auto_now_add=True)
    properties = models.JSONField(default=dict)

    class Meta:
        unique_together = ('customer', 'product')

    def subtotal(self):
        return self.quantity * self.product.price


class Order(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('processing', 'Processing'),
        ('shipped', 'Shipped'),
        ('delivered', 'Delivered'),
        ('cancelled', 'Cancelled'),
        ('returned', 'Returned'),
    ]

    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name='orders')
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name='invoices')
    order_id = models.SlugField(unique=True, editable=False)
    shipping_address = models.ForeignKey(Address, on_delete=models.SET_NULL, null=True)
    total_amount = models.DecimalField(max_digits=10, decimal_places=2)
    sub_total = models.DecimalField(max_digits=10, decimal_places=2, default = 0)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    shipping_method = models.CharField(max_length=50, default='')
    shipping_amount = models.DecimalField(max_digits=10,decimal_places=2, default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if not self.order_id:
            self.order_id = slugify(f"{self.customer.user.username}-{self.pk or ''}")[:50]
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Order {self.order_id} ({self.customer})"


class OrderItem(models.Model):
    """Individual items in an order."""
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('processing', 'Processing'),
        ('shipped', 'Shipped'),
        ('delivered', 'Delivered'),
        ('cancelled', 'Cancelled'),
        ('returned', 'Returned'),
    ]
    COLLECTION_ORIGIN_STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('processing', 'Processing'),
        ('shipped', 'Shipped'),
        ('received', 'Received'),
        ('approved', 'Approved'),
        ('cancelled', 'Cancelled'),
        ('returned', 'Returned'),
    ]
    COLLECTION_DESTINATION_STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('processing', 'Processing'),
        ('shipped', 'Shipped'),
        ('received', 'Received'),
        ('approved', 'Approved'),
        ('cancelled', 'Cancelled'),
        ('returned', 'Returned'),
    ]
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='items')
    color = models.ForeignKey(Color, on_delete=models.SET_NULL, null=True, default = None, related_name='color')
    size = models.ForeignKey(Sizes, on_delete=models.SET_NULL, null=True,default=None, related_name='sizes')
    product = models.ForeignKey(Product, on_delete=models.SET_NULL, null=True)
    quantity = models.PositiveIntegerField(default=1)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    sub_total = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    properties = models.JSONField(default=dict, editable=False)
    tracking_number = models.CharField(max_length=100, unique=True, default='')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    designer_status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    collection_destination_status = models.CharField(max_length=20, choices=COLLECTION_DESTINATION_STATUS_CHOICES, default='pending')
    collection_origin_status = models.CharField(max_length=20, choices=COLLECTION_ORIGIN_STATUS_CHOICES, default='pending')
    created_at = models.DateTimeField(auto_now_add=True, null= True)

    def subtotal(self):
        return self.quantity * self.amount


class ReturnRequest(models.Model):
    """Return request for an order item."""
    order_item = models.ForeignKey(OrderItem, on_delete=models.CASCADE, related_name='return_requests')
    reason = models.TextField()
    is_approved = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(blank=True, null=True)
    return_id= models.CharField(max_length=20, default='', blank=True)
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('canceled', 'Canceled'),
        ('returned', 'Returned'),
        ('rejected', 'Rejected')
    ]
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    designer_status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    admin_status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')

    def __str__(self):
        return f"Return {self.order_item.product.name} ({self.status})"

    def save(self,*args, **kwargs):
        if not self.return_id:
            for _ in range(5):
                return_id = f'URBRE_{generate_random_numbers(10)}'
                try:
                    ReturnRequest.objects.get(return_id = return_id)
                except ObjectDoesNotExist:
                    self.return_id = return_id
                    break
        super().save(*args, **kwargs)


# Add after existing models


class OrderTracking(models.Model):
    """Tracks the status of an order in detail."""
    order = models.OneToOneField(Order, on_delete=models.CASCADE, related_name='tracking')
    tracking_number = models.CharField(max_length=100, unique=True)
    carrier = models.CharField(max_length=100, blank=True, null=True)
    current_status = models.CharField(max_length=50, default='Pending')
    last_updated = models.DateTimeField(auto_now=True)
    estimated_delivery = models.DateField(blank=True, null=True)

    def __str__(self):
        return f"Tracking {self.tracking_number} ({self.order.order_id})"
