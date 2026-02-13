from django.test import TestCase

# Create your tests here.

from django.db import migrations
import django.core.exceptions

def set_payment_month(apps, schema_editor):
    # We can't import the Person model directly as it may be a newer
    # version than this migration expects. We use the historical version.
    months = ['JAN','FEB','MAR','APR','MAY','JUN','JUL','AUG','SEP','OCT','NOV','DEC']
    payments = apps.get_model('partners', 'DpPayments')
    for payment in payments.objects.filter(synced=False):
        if len(payment.payment_year) != 4:
            payment.payment_year = '2021'
        payment.payment_month=payment.payment_month.upper()
        try:
            payment.month_covered= str(payment.payment_year)+'-'+str(months.index(payment.payment_month)+1)+'-'+'1'
        except django.core.exceptions.ValidationError:
            pass
        payment.synced = True
        payment.save()



class Migration(migrations.Migration):

    dependencies = [
        ('partners', '0026_dppayments_synced'),
    ]

    operations = [
                migrations.RunPython(set_payment_month),

    ]
