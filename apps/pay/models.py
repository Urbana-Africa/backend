from apps.authentication.models import User
from datetime import datetime
from django.core.exceptions import ObjectDoesNotExist
# from users import models as cmodels
from django.utils.crypto import get_random_string
from apps.utils.uuid_generator import generate_custom_id
from django.utils import timezone
from django.db import models, transaction
from django.core.exceptions import ValidationError



class Currency(models.Model):
    
    id = models.CharField(
        primary_key=True,
        max_length=50,
        default=generate_custom_id,
        editable=False,
    )
    name = models.CharField(default='',max_length=10,blank=True)
    symbol = models.CharField(default='',max_length=10,blank=True)

    def __str__(self) -> str:
        return self.name



class Banks(models.Model):
    id = models.CharField(
        primary_key=True,
        max_length=50,
        default=generate_custom_id,
        editable=False,
    )
    name = models.CharField(default='',max_length=200,blank=True)
    code = models.CharField(default='',max_length=200,blank=True)
    logo = models.FileField(upload_to='banks/logos/', max_length=10,blank=True)

    def __str__(self) -> str:
        return self.name


    class Meta:
        
        db_table = 'banks'


PROCESSORS = (
    ('paystack', 'Paystack'),
    ('stripe', 'Stripe'),
    ('paypal', 'PayPal'),
    ('manual', 'Manual'),
)

PAYMENT_TYPES = (
    ('cheque', 'Cheque'),
    ('bank_transfer', 'Bank Transfer'),
    ('online', 'Online'),
    ('pos', 'P.O.S'),
)

PAYMENT_STATUS = (
    ('pending', 'Pending'),
    ('success', 'Success'),
    ('failed', 'Failed'),
    ('expired', 'Expired'),
)


def generate_unique_reference():
    """Generate a truly unique reference for online payment."""
    while True:
        reference = get_random_string(16).upper()
        if not Payment.objects.filter(reference=reference).exists():
            return reference


class PaymentAttempt(models.Model):
    """
    Represents each attempt to pay for a invoice or any service.
    """
    id = models.CharField(primary_key=True, max_length=50, default=generate_custom_id, editable=False)
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    reference = models.CharField(max_length=100, unique=True, blank=True)
    processor = models.CharField(max_length=20, choices=PROCESSORS, blank=True, default='')
    processor_payment_id = models.CharField(max_length=200, blank=True, default='')
    currency = models.CharField(max_length=10, default='NGN', blank=True)
    status = models.CharField(max_length=50, choices=PAYMENT_STATUS, default='pending')
    is_successful = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        # Generate unique reference if not set
        if not self.reference:
            while True:
                ref = get_random_string(50)
                if not PaymentAttempt.objects.filter(reference=ref).exists():
                    self.reference = ref
                    break
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.reference} - {self.processor} - {self.status}"


class Payment(models.Model):
    id = models.CharField(primary_key=True, max_length=50, default=generate_custom_id, editable=False)
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    payment_method = models.CharField(max_length=50) 
    name = models.CharField(max_length=100, null=True, blank=True)
    reference = models.CharField(max_length=100, unique=True, blank=True)
    processor = models.CharField(max_length=20, choices=PROCESSORS, blank=True, default='')
    processor_payment_id = models.CharField(max_length=200, blank=True, default='')  # Processor’s internal ID
    currency = models.CharField(max_length=10, default='NGN', blank=True)
    status = models.CharField(max_length=50, choices=PAYMENT_STATUS, default='pending')
    is_paid = models.BooleanField(default=False)
    approved = models.BooleanField(default=False)
    is_deleted = models.BooleanField(default=False)
    date_time_added = models.DateTimeField(auto_now_add=True)
    date_time_paid = models.DateTimeField(null=True, blank=True)
    date_time_approved = models.DateTimeField(null=True, blank=True)

    def save(self, *args, **kwargs):
        # Auto-generate unique reference if not set
        if not self.reference:
            exist = True
            while exist:
                ref = get_random_string(50)
                if not Payment.objects.filter(reference=ref).exists():
                    self.reference = ref
                    exist = False
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.reference} - {self.amount}"

    class Meta:
        db_table = 'payments'
        ordering = ['-date_time_added']

    def __str__(self):
        return f"{self.reference} - {self.amount} ({self.processor or 'N/A'})"

    def save(self, *args, **kwargs):
        # Auto-generate amount for known plans
        if self.name:
            plan = self.name.lower().strip()
            if plan == 'basic':
                self.amount = 4000
            elif plan == 'premium':
                self.amount = 7000

        # Generate unique reference if missing
        if not self.reference:
            self.reference = generate_unique_reference()

        # Auto-mark paid if status is success
        if self.status == 'success' and not self.is_paid:
            self.is_paid = True
            self.date_time_paid = datetime.now()

        super().save(*args, **kwargs)

    def mark_as_paid(self, processor_id=None):
        """Convenience method for confirming successful payment."""
        self.status = 'success'
        self.is_paid = True
        self.date_time_paid = datetime.now()
        if processor_id:
            self.processor_payment_id = processor_id
        self.save(update_fields=['status', 'is_paid', 'date_time_paid', 'processor_payment_id'])

    def soft_delete(self, reason=''):
        """Soft delete the payment record instead of removing it."""
        self.is_deleted = True
        self.delete_reason = reason
        self.save(update_fields=['is_deleted', 'delete_reason'])




class Invoice(models.Model):
    id = models.CharField(primary_key=True, max_length=50, default=generate_custom_id, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    
    # The successful payment
    payment = models.ForeignKey(Payment, null=True, blank=True, on_delete=models.SET_NULL)
    # All attempts (successful or failed)
    payment_attempts = models.ManyToManyField(PaymentAttempt, blank=True, related_name="invoices")
    amount = models.PositiveIntegerField(default=0)
    start_date = models.DateField(null=True)
    expiry_date = models.DateField(null=True)
    is_active = models.BooleanField(default=False)
    purpose = models.CharField(max_length=200, default='Invoice',)
    is_expired = models.BooleanField(default=False)
    is_deleted = models.BooleanField(default=False)
    is_used = models.BooleanField(default=False)
    expire_notification_sent = models.BooleanField(default=False)
    date_time_added = models.DateTimeField(default=timezone.now)


    def __str__(self):
        return f"{self.user} - {self.start_date} to {self.expiry_date}"



    class Meta:
        
        db_table = 'invoices'


    def __str__(self):
        try:
            return f"{self.user.get_full_name()}"
        except Exception:
            return str(self.id)
        

    def save(self,*args,**kwargs):
        if self.expiry_date:
            if self.expiry_date < datetime.now().date():
                self.is_active = False
                self.is_expired = True  
                self.is_used = True  
            else:
                self.is_active = True
                self.is_expired = False  
                self.is_used = False  
        super(Invoice,self).save()



class PaymentWebhookLog(models.Model):
    """
    Stores every webhook event received from payment processors.
    Useful for auditing, debugging, and preventing duplicate processing.
    """
    id = models.CharField(primary_key=True, max_length=50, default=generate_custom_id, editable=False)
    processor = models.CharField(max_length=50)
    event_type = models.CharField(max_length=100, blank=True)
    reference = models.CharField(max_length=100, blank=True)
    raw_payload = models.JSONField()
    status_code = models.IntegerField(default=200)
    processed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'payment_webhook_logs'
        ordering = ['-created_at']

    def mark_processed(self):
        """Mark webhook as processed successfully."""
        self.processed = True
        self.processed_at = timezone.now()  
        self.save(update_fields=["processed", "processed_at"])

    def __str__(self):
        return f"{self.processor.upper()} - {self.event_type} - {self.reference or 'N/A'}"
    
    
class Transfers(models.Model):
    id = models.CharField(
        primary_key=True,
        max_length=50,
        default=generate_custom_id,
        editable=False,
    )
    user=models.ForeignKey(User,on_delete=models.SET_NULL,null=True,blank=True)
    amount = models.IntegerField(default=0,blank=True)
    transfer_ref = models.CharField(max_length=100,default='', blank=True,)
    transfer_id = models.CharField(max_length=100,default='',blank=True,)
    date_time_added = models.DateTimeField(auto_now=True)
    is_approved = models.BooleanField(default=False)
    is_deleted = models.BooleanField(default=False)
    description = models.TextField(default='',max_length=1000,blank=True)
    currency = models.CharField(default='',max_length=10,blank=True)
    status = models.CharField(default='status',max_length=500,blank=True)
    bank_code = models.CharField(default='',max_length=100,blank=True)
    bank_name = models.CharField(default='',max_length=100,blank=True)
    account_number = models.CharField(default='',blank=True,max_length=500)
    account_name = models.CharField(default='',blank=True,max_length=500)
    recipient_code = models.CharField(max_length=200,default='',blank=True,)
    delete_reason = models.CharField(max_length=100,default='', blank=True,)



    class Meta:
        
        db_table = 'transfers'

    def __str__(self):
        return self.transfer_id + ' '+ str(self.amount)

    def save(self,*args, **kwargs):
        super(Transfers,self).save( *args, **kwargs)
        self.formatted_amount = '{:,.2f}'.format(self.amount)
        if not self.transfer_id:
            exist = True
            while exist:
                transfer_id = get_random_string(50)
                try:                
                    Transfers.objects.get(transfer_id=transfer_id)

                except ObjectDoesNotExist:
                    self.transfer_id=transfer_id
                    exist = False
                    break
        super(Transfers,self).save()


    def delete(self):
        self.is_deleted = True

        super(Transfers,self).save()

class PartnerCommisions(models.Model):
    id = models.CharField(
        primary_key=True,
        max_length=50,
        default=generate_custom_id,
        editable=False,
    )
    user=models.ForeignKey(User,on_delete=models.SET_NULL,null=True,blank=False)
    amount = models.IntegerField(default=0,blank=True)
    payment_ref = models.CharField(max_length=100,default='',blank=True,)
    date_time_added = models.DateTimeField(auto_now=True)
    is_deleted = models.BooleanField(default=False)
    comment = models.CharField(default='No Comment',max_length=500,blank=True)
    purpose = models.CharField(default='',max_length=500,blank=True)
    service = models.CharField(default='',max_length=500,blank=True)
    currency = models.CharField(default='',max_length=10,blank=True)
    payment = models.ForeignKey(Payment,on_delete=models.SET_NULL,null=True,blank=True)
    is_paid = models.BooleanField(default=False)


    class Meta:
        
        db_table = 'partnercommisions'


    def __str__(self):
        return self.payment_ref + ' '+ str(self.amount)

    def save(self,*args, **kwargs):
        if not self.payment_ref:
            exist = True
            while exist:
                payment_ref = get_random_string(50)
                try:                
                    PartnerCommisions.objects.get(payment_ref=payment_ref)

                except ObjectDoesNotExist:
                    self.payment_ref=payment_ref
                    exist = False
                    break
        super(PartnerCommisions,self).save()



# =============================
# CHOICES
# =============================

TRANSACTION_TYPES = (
    ("escrow_hold", "Escrow Hold"),
    ("escrow_release", "Escrow Release"),
    ("withdrawal", "Withdrawal"),
    ("commission", "Platform Commission"),
    ("refund", "Refund"),
)

TRANSACTION_STATUS = (
    ("pending", "Pending"),
    ("completed", "Completed"),
    ("failed", "Failed"),
)

ESCROW_STATUS = (
    ("held", "Held"),
    ("released", "Released"),
    ("refunded", "Refunded"),
)

WITHDRAWAL_STATUS = (
    ("pending", "Pending"),
    ("processing", "Processing"),
    ("completed", "Completed"),
    ("failed", "Failed"),
    ("rejected", "Rejected"),
)


# =============================
# WALLET
# =============================

class Wallet(models.Model):
    id = models.CharField(primary_key=True, max_length=50, default=generate_custom_id, editable=False)
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="wallet")
    currency = models.CharField(max_length=10, default="NGN")
    is_locked = models.BooleanField(default= False)
    available_balance = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    pending_balance = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "wallets"

    def __str__(self):
        return f"{self.user.get_full_name()} Wallet"


# =============================
# WALLET TRANSACTION (LEDGER)
# =============================

class WalletTransaction(models.Model):
    id = models.CharField(primary_key=True, max_length=50, default=generate_custom_id, editable=False)

    wallet = models.ForeignKey(Wallet, on_delete=models.CASCADE, related_name="transactions")
    user = models.ForeignKey(User, on_delete=models.CASCADE)

    transaction_type = models.CharField(max_length=50, choices=TRANSACTION_TYPES)
    status = models.CharField(max_length=50, choices=TRANSACTION_STATUS, default="pending")

    amount = models.DecimalField(max_digits=14, decimal_places=2)
    reference = models.CharField(max_length=120, unique=True)

    description = models.TextField(blank=True)

    related_payment = models.ForeignKey("Payment", null=True, blank=True, on_delete=models.SET_NULL)
    related_order_id = models.CharField(max_length=100, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "wallet_transactions"
        ordering = ["-created_at"]

    def mark_completed(self):
        self.status = "completed"
        self.completed_at = timezone.now()
        self.save(update_fields=["status", "completed_at"])


# =============================
# ESCROW (Customer Funds Held)
# =============================

class Escrow(models.Model):
    id = models.CharField(primary_key=True, max_length=50, default=generate_custom_id, editable=False)

    order_id = models.CharField(max_length=100)
    payment = models.ForeignKey("Payment", on_delete=models.CASCADE)

    customer = models.ForeignKey(User, on_delete=models.CASCADE, related_name="customer_escrows")
    designer = models.ForeignKey(User, on_delete=models.CASCADE, related_name="designer_escrows")

    amount = models.DecimalField(max_digits=14, decimal_places=2)
    platform_commission = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    status = models.CharField(max_length=50, choices=ESCROW_STATUS, default="held")

    created_at = models.DateTimeField(auto_now_add=True)
    released_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "escrows"

    @transaction.atomic
    def release_funds(self):
        if self.status != "held":
            raise ValidationError("Escrow already processed")

        designer_wallet, _ = Wallet.objects.get_or_create(user=self.designer)

        designer_share = self.amount - self.platform_commission

        # Move pending → available
        designer_wallet.available_balance += designer_share
        designer_wallet.save()

        WalletTransaction.objects.create(
            wallet=designer_wallet,
            user=self.designer,
            transaction_type="escrow_release",
            status="completed",
            amount=designer_share,
            reference=f"ESCROW-{self.id}",
            related_payment=self.payment,
            related_order_id=self.order_id,
        )

        self.status = "released"
        self.released_at = timezone.now()
        self.save(update_fields=["status", "released_at"])


# =============================
# WITHDRAWALS (Flutterwave)
# =============================

class Withdrawal(models.Model):
    id = models.CharField(primary_key=True, max_length=50, default=generate_custom_id, editable=False)

    wallet = models.ForeignKey(Wallet, on_delete=models.CASCADE)
    user = models.ForeignKey(User, on_delete=models.CASCADE)

    amount = models.DecimalField(max_digits=14, decimal_places=2)

    status = models.CharField(max_length=50, choices=WITHDRAWAL_STATUS, default="pending")
    reference = models.CharField(max_length=120, unique=True)

    flutterwave_transfer_id = models.CharField(max_length=200, blank=True)

    bank_name = models.CharField(max_length=100)
    bank_code = models.CharField(max_length=50)
    account_number = models.CharField(max_length=20)
    account_name = models.CharField(max_length=200)
    
    created_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "withdrawals"

    @transaction.atomic
    def process_withdrawal(self):
        if self.wallet.available_balance < self.amount:
            raise ValidationError("Insufficient balance")

        self.wallet.available_balance -= self.amount
        self.wallet.is_locked = True
        self.wallet.save()

        WalletTransaction.objects.create(
            wallet=self.wallet,
            user=self.user,
            transaction_type="withdrawal",
            status="completed",
            amount=self.amount,
            reference=f"WDR-{self.id}",
            description="Withdrawal to bank"
        )

        self.status = "processing"
        self.save(update_fields=["status"])