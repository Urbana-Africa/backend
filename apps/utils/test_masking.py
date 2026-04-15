import pytest
from apps.utils.masking import generate_masked_email, generate_masked_phone

def test_generate_masked_email():
    email = "john.doe@example.com"
    masked = generate_masked_email(email)
    
    assert "@urbanashops.com" in masked
    assert "joh" in masked
    assert masked != email

def test_generate_masked_phone():
    phone = "+234 801 234 5678"
    masked = generate_masked_phone(phone)
    
    assert "**** 5678" == masked
    
def test_generate_masked_phone_short():
    phone = "123"
    masked = generate_masked_phone(phone)
    
    assert "****" == masked
