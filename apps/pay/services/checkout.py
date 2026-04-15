from decimal import Decimal
from django.db import transaction
from apps.customers.models import Order
from apps.pay.models import Escrow

def complete_successful_payment(payment, invoice):
    """
    Called after a payment is successfully verified.
    Links payment to invoice, and distributes the payment into 
    separate Escrow records for each Designer's OrderItem.
    """
    with transaction.atomic():
        invoice.payment = payment
        invoice.is_active = True
        invoice.is_used = True
        invoice.save()

        # Find the order associated with this invoice
        try:
            order = Order.objects.get(invoice=invoice)
        except Order.DoesNotExist:
            # Invoice might not be for a product order, return early
            return

        # Distribute into Escrow for each OrderItem (Sub-Order)
        for item in order.items.all():
            if not item.escrow:
                # 10% platform commission logic as defined in requirements
                commission = item.sub_total * Decimal("0.10")
                
                escrow = Escrow.objects.create(
                    payment=payment,
                    customer=order.customer.user,
                    designer=item.designer,
                    amount=item.sub_total,
                    platform_commission=commission,
                    status="held"
                )
                
                # Link escrow to the sub-order
                item.escrow = escrow
                item.save(update_fields=['escrow'])
