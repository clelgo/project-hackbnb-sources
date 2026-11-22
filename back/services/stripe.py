import os
import stripe
import requests

def create_checkout(reference, name, unit_price, quantity):
    """
        Create Stripe checkout
        Documentation: https://docs.stripe.com/checkout/quickstart?client=react&lang=python
    """
    
    stripe.api_key = os.getenv('STRIPE_API_KEY')
    origin_host = os.getenv('APP_ORIGIN_HOST')
    return stripe.checkout.Session.create(
        mode='payment',
        # Stripe replaces the placeholder with the Checkout session ID.
        # The session ID is not a secret: payment is verified server-side.
        success_url=f"{origin_host}/booking/{reference}?checkout_session_id={{CHECKOUT_SESSION_ID}}",
        cancel_url=origin_host,
        client_reference_id=reference,
        line_items=[
            {
                'quantity': quantity,
                'price_data': {
                    'currency': 'EUR',
                    'unit_amount': round(unit_price * 100),
                    'product_data': {
                        'name': name,
                    },
                },
            },
        ],
    )

def get_checkout_session(checkout_session_id):
    """
        Retrieve a Checkout session to verify its payment status server-side.
    """
    stripe.api_key = os.getenv('STRIPE_API_KEY')
    return stripe.checkout.Session.retrieve(checkout_session_id)


def get_checkout_lines(checkout_session_id):
    url = f"https://api.stripe.com/v1/checkout/sessions/{checkout_session_id}/line_items"

    session = requests.Session()
    session.auth = (os.getenv('STRIPE_API_KEY'), "")
    return session.get(url).json()["data"]
