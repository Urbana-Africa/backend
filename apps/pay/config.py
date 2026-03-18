from decouple import config
import requests

ENV = config("ENV", default="dev")

def get_paystack_keys():
    if ENV == "prod":
        return {
            "public_key": config("PAYSTACK_PUBLIC_KEY"),
            "secret_key": config("PAYSTACK_SECRET_KEY"),   
        }
    return  {
        "public_key": config("PAYSTACK_TEST_PUBLIC_KEY"),
        "secret_key": config("PAYSTACK_TEST_SECRET_KEY"),}

def get_flutterwave_keys():
    if ENV == "prod":
        return {
            "public_key": config("FLUTTERWAVE_PUBLIC_KEY"),
            "secret_key": config("FLUTTERWAVE_SECRET_KEY"),
            "encryption_key": config("FLUTTERWAVE_ENCRYPTION_KEY"),
            "base": "https://api.flutterwave.com/v3",
        }
    return {
        "public_key": config("FLUTTERWAVE_TEST_PUBLIC_KEY"),
        "secret_key": config("FLUTTERWAVE_TEST_SECRET_KEY"),
        "encryption_key": config("FLUTTERWAVE_TEST_ENCRYPTION_KEY"),
        "base": "https://api.flutterwave.com/v3",
    }


def get_stripe_keys():
    if ENV == "prod":
        return {
            "public_key": config("STRIPE_PUBLIC_KEY"),
            "secret_key": config("STRIPE_SECRET_KEY"),
            "encryption_key": config("STRIPE_ENCRYPTION_KEY"),
            "base": "https://api.stripe.com/v3",
        }
    return {
        "public_key": config("STRIPE_TEST_PUBLIC_KEY"),
        "secret_key": config("STRIPE_TEST_SECRET_KEY"),
        "encryption_key": config("STRIPE_TEST_ENCRYPTION_KEY"),
        "base": "https://api.stripe.com/v3",
    }


def get_flutterwave_token():
    
    url = f"https://idp.flutterwave.com/realms/flutterwave/protocol/openid-connect/token"
    headers = {
    "Content-Type": "application/x-www-form-urlencoded"
    }
    fw_keys = get_flutterwave_keys()  

    payload = {
        "client_id":fw_keys['client_id'],
        "client_secret": fw_keys['client_secret'],
        "grant_type": "client_credentials"
    }

    response = requests.post(url, data=payload,headers=headers)
    data = response.json()
    print(data)

    return data


def get_flutterwave_token():
    
    url = f"https://idp.flutterwave.com/realms/flutterwave/protocol/openid-connect/token"
    headers = {
    "Content-Type": "application/x-www-form-urlencoded"
    }
    fw_keys = get_flutterwave_keys()  

    payload = {
        "client_id":fw_keys['client_id'],
        "client_secret": fw_keys['client_secret'],
        "grant_type": "client_credentials"
    }

    response = requests.post(url, data=payload,headers=headers)
    data = response.json()
    print(data)

    return data