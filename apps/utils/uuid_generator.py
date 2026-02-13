import secrets
import string


def generate_custom_id(length=30):
    """Generate a random alphanumeric ID of specified length."""
    characters = string.ascii_letters + string.digits
    return ''.join(secrets.choice(characters) for _ in range(length))


def generate_random_numbers(length=10):
    """Generate a random alphanumeric ID of specified length."""
    characters = string.digits
    return ''.join(secrets.choice(characters) for _ in range(length))

