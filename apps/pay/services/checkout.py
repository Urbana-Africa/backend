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
                # Resolve dynamic pricing splits from item properties
                props = item.properties or {}
                if 'base_price' in props:
                    qty = Decimal(str(item.quantity))
                    base_price = Decimal(str(props['base_price']))
                    platform_margin = Decimal(str(props.get('platform_margin', 0)))
                    duties_buffer = Decimal(str(props.get('duties_buffer', 0)))
                    
                    designer_base = (base_price - platform_margin) * qty
                    commission = (platform_margin + duties_buffer) * qty
                    escrow_amount = designer_base + commission
                else:
                    # Fallback to legacy 10% platform commission logic
                    escrow_amount = item.sub_total
                    commission = item.sub_total * Decimal("0.10")
                
                escrow = Escrow.objects.create(
                    payment=payment,
                    customer=order.customer.user,
                    designer=item.designer,
                    amount=escrow_amount,
                    platform_commission=commission,
                    status="held"
                )
                
                # Link escrow to the sub-order
                item.escrow = escrow
                item.save(update_fields=['escrow'])
