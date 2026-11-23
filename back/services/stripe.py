import os
import stripe

try:
    from stripe import StripeError
except ImportError:  # anciennes versions du SDK
    from stripe.error import StripeError


def create_checkout(reference, name, unit_price, quantity):
    """
        Create Stripe checkout
        Documentation: https://docs.stripe.com/checkout/quickstart?client=react&lang=python
    """
    stripe.api_key = os.getenv('STRIPE_API_KEY')
    origin_host = os.getenv('APP_ORIGIN_HOST')

    return stripe.checkout.Session.create(
        mode='payment',
        success_url=f"{origin_host}/booking/{reference}?checkout_session_id={{CHECKOUT_SESSION_ID}}",
        cancel_url=origin_host,
        client_reference_id=reference,
        metadata={'reference': reference},
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
    stripe.api_key = os.getenv('STRIPE_API_KEY')
    return stripe.checkout.Session.retrieve(checkout_session_id)


def get_checkout_lines(checkout_session_id):
    stripe.api_key = os.getenv('STRIPE_API_KEY')
    return stripe.checkout.Session.list_line_items(checkout_session_id).data