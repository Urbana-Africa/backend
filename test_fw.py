import os
import sys
import django
import requests

# Setup Django environment
sys.path.append(os.getcwd())
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'urbana.settings')
django.setup()

from apps.pay.config import get_flutterwave_keys
from apps.pay.services.withdrawals import _fw_headers, FW_BASE_URL

def test_fw_connection():
    try:
        keys = get_flutterwave_keys()
        print(f"Keys found: {list(keys.keys())}")
        print(f"Secret prefix: {keys.get('secret_key', '')[:10]}...")
        
        # Try a simple GET request to list banks
        print("Testing Flutterwave connection (GET /banks/NG)...")
        resp = requests.get(
            f"{FW_BASE_URL}/banks/NG",
            headers=_fw_headers(),
            timeout=15
        )
        print(f"Status Code: {resp.status_code}")
        data = resp.json()
        if data.get("status") == "success":
            print("✅ Connection successful!")
            print(f"Found {len(data.get('data', []))} banks.")
        else:
            print("❌ Connection failed!")
            print(f"Message: {data.get('message')}")
            
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    test_fw_connection()
