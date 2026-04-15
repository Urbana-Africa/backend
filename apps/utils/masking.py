import uuid
import re

def generate_masked_email(original_email, suffix="@urbanashops.com"):
    """
    Generates a forwarding email alias that hides the real customer email.
    The real email should be stored in the User/Customer profile, but OrderItems
    exposed to designers will use this masked email.
    """
    try:
        if not original_email or "@" not in original_email:
            return f"customer-{uuid.uuid4().hex[:8]}{suffix}"
            
        username_part = original_email.split("@")[0]
        # Take up to 3 chars of the original username for a slight human touch, then uuid
        masked_prefix = username_part[:3] if len(username_part) >= 3 else username_part
        return f"{masked_prefix}-{uuid.uuid4().hex[:6]}{suffix}"
    except Exception:
        return f"customer-{uuid.uuid4().hex[:8]}{suffix}"

def generate_masked_phone(original_phone):
    """
    Masks all but the last 4 digits of a phone number.
    E.g., +234 801 234 5678 -> **** 5678
    """
    if not original_phone:
        return "N/A"
        
    try:
        # Remove non-alphanumeric characters except +
        clean_phone = re.sub(r'[^\d\+\s]', '', str(original_phone)).strip()
        
        if len(clean_phone) <= 4:
            return "****"
            
        return f"**** {clean_phone[-4:]}"
    except Exception:
        return "****"
