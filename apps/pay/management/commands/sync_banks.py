import requests
from django.core.management.base import BaseCommand
from apps.pay.models import Banks
from apps.pay.config import get_flutterwave_keys

class Command(BaseCommand):
    help = "Sync official bank list from Flutterwave to the local database."

    def add_arguments(self, parser):
        parser.add_argument("--country", type=str, default="NG", help="Country code (default: NG)")

    def handle(self, *args, **options):
        country = options["country"].upper()
        self.stdout.write(f"Syncing banks for {country} from Flutterwave...")

        keys = get_flutterwave_keys()
        url = f"https://api.flutterwave.com/v3/banks/{country}"
        headers = {
            "Authorization": f"Bearer {keys['secret_key']}",
        }

        try:
            resp = requests.get(url, headers=headers, timeout=20)
            resp.raise_for_status()
            data = resp.json()

            if data.get("status") == "success":
                banks_list = data.get("data", [])
                self.stdout.write(f"Found {len(banks_list)} banks. Updating database...")

                created_count = 0
                updated_count = 0

                for b in banks_list:
                    bank_name = b.get("name")
                    bank_code = b.get("code")

                    # We use bank name and code as unique markers for existing ones
                    # or handle by ID if we want persistence. Here we use code.
                    bank_obj, created = Banks.objects.update_or_create(
                        code=bank_code,
                        defaults={
                            "name": bank_name,
                        }
                    )
                    if created:
                        created_count += 1
                    else:
                        updated_count += 1

                self.stdout.write(self.style.SUCCESS(
                    f"Successfully synced {country} banks. Created: {created_count}, Updated: {updated_count}"
                ))
            else:
                self.stderr.write(self.style.ERROR(f"Flutterwave error: {data.get('message')}"))

        except Exception as e:
            self.stderr.write(self.style.ERROR(f"Error syncing banks: {str(e)}"))
