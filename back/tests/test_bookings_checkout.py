import random
import re
import secrets
import string
from unittest.mock import Mock

import pytest
from flask import Flask

from api.bookings.bookings_api import bookings_api
from api.bookings.bookings_models import Booking
from api.rooms.rooms_models import Room
from api.users.users_models import User
from database.init import db
from services.stripe import create_checkout, get_checkout_session


class FakeCheckoutSession:
    _next_id = 0

    def __init__(self):
        type(self)._next_id += 1
        self.id = f"cs_test_fake_session_{self._next_id}"
        self.url = f"https://checkout.stripe.com/c/pay/{self.id}"


class FakeStripeSession:
    def __init__(self, session_id, payment_status="paid", client_reference_id=None):
        self.id = session_id
        self.payment_status = payment_status
        self.client_reference_id = client_reference_id


@pytest.fixture
def app():
    app = Flask(__name__)
    app.config.update(
        TESTING=True,
        SQLALCHEMY_DATABASE_URI="sqlite://",
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
    )
    db.init_app(app)
    app.register_blueprint(bookings_api, url_prefix="/bookings")

    with app.app_context():
        db.create_all()
        db.session.add(User(
            id=1,
            name="Host",
            email="host@example.com",
            password_hash="not-used",
        ))
        db.session.add(Room(
            id=1,
            title="Test room",
            city="Test city",
            price=100.0,
            picture="room.jpg",
            category="test",
            description="Test description",
            host_user_id=1,
        ))
        db.session.commit()

    yield app

    with app.app_context():
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


def create_booking(client, monkeypatch):
    captured = {}
    sessions = []

    def fake_create_checkout(**kwargs):
        captured.update(kwargs)
        session = FakeCheckoutSession()
        sessions.append(session)
        return session

    monkeypatch.setattr(
        "api.bookings.bookings_api.create_checkout",
        fake_create_checkout,
    )

    response = client.post("/bookings/", json={
        "roomId": 1,
        "arrivalDate": "2026-12-01",
        "departureDate": "2026-12-03",
        "guestsCount": 2,
    })
    assert response.status_code == 200

    return response.get_json(), sessions[-1], captured


def confirm_checkout(client, reference, payload):
    return client.post(
        f"/bookings/{reference}/confirm-checkout",
        json=payload,
    )


def test_create_checkout_uses_stripe_session_id_placeholder(monkeypatch):
    captured = {}

    def fake_stripe_create(**kwargs):
        captured.update(kwargs)
        return Mock(id="cs_test_123", url="https://checkout.stripe.com/c/pay/cs_test_123")

    monkeypatch.setenv("APP_ORIGIN_HOST", "http://localhost:9000")
    monkeypatch.setenv("STRIPE_API_KEY", "sk_test_fake")
    monkeypatch.setattr(
        "services.stripe.stripe.checkout.Session.create",
        fake_stripe_create,
    )

    create_checkout(
        reference="ABC12345",
        name="2 nuits à Test room (Test city)",
        unit_price=100,
        quantity=2,
    )

    assert captured["success_url"] == (
        "http://localhost:9000/booking/ABC12345"
        "?checkout_session_id={CHECKOUT_SESSION_ID}"
    )
    assert "confirmation_secret" not in captured["success_url"]
    assert captured["client_reference_id"] == "ABC12345"


def test_new_booking_does_not_create_a_confirmation_secret(app, client, monkeypatch):
    body, session, captured = create_booking(client, monkeypatch)

    assert "confirmation_secret" not in captured
    assert body["checkout_url"] == session.url

    with app.app_context():
        booking = Booking.query.filter_by(reference=body["reference"]).one()
        assert booking.stripe_checkout_session_id == session.id
        # Legacy column: kept for schema compatibility, but no secret is stored.
        assert booking.checkout_confirmation_secret == ""


def test_booking_reference_uses_csprng(client, monkeypatch):
    def forbidden_random_choice(*args, **kwargs):
        raise AssertionError("random.choice must not be used for booking references")

    monkeypatch.setattr(random, "choice", forbidden_random_choice)

    calls = []
    original_secrets_choice = secrets.choice

    def recording_secrets_choice(alphabet):
        calls.append(alphabet)
        return original_secrets_choice(alphabet)

    monkeypatch.setattr(secrets, "choice", recording_secrets_choice)

    body, _, _ = create_booking(client, monkeypatch)

    expected_alphabet = string.ascii_uppercase + string.digits
    assert len(calls) == 8
    assert all(alphabet == expected_alphabet for alphabet in calls)
    assert re.fullmatch(r"[A-Z0-9]{8}", body["reference"])


def test_booking_reference_format_and_uniqueness(client, monkeypatch):
    references = {
        create_booking(client, monkeypatch)[0]["reference"]
        for _ in range(10)
    }

    assert len(references) == 10
    assert all(re.fullmatch(r"[A-Z0-9]{8}", reference) for reference in references)


def test_confirm_checkout_accepts_paid_matching_session(app, client, monkeypatch):
    body, session, _ = create_booking(client, monkeypatch)
    retrieved = []

    def fake_get_checkout_session(session_id):
        retrieved.append(session_id)
        return FakeStripeSession(
            session_id,
            payment_status="paid",
            client_reference_id=body["reference"],
        )

    monkeypatch.setattr(
        "api.bookings.bookings_api.get_checkout_session",
        fake_get_checkout_session,
    )

    response = confirm_checkout(
        client,
        body["reference"],
        {"checkoutSessionId": session.id},
    )

    assert response.status_code == 200
    assert response.get_json() == {"success": True}
    assert retrieved == [session.id]
    with app.app_context():
        booking = Booking.query.filter_by(reference=body["reference"]).one()
        assert booking.is_paid is True


def test_confirm_checkout_rejects_unpaid_session(app, client, monkeypatch):
    body, session, _ = create_booking(client, monkeypatch)
    monkeypatch.setattr(
        "api.bookings.bookings_api.get_checkout_session",
        lambda session_id: FakeStripeSession(
            session_id,
            payment_status="unpaid",
            client_reference_id=body["reference"],
        ),
    )

    response = confirm_checkout(
        client,
        body["reference"],
        {"checkoutSessionId": session.id},
    )

    assert response.status_code == 422
    with app.app_context():
        booking = Booking.query.filter_by(reference=body["reference"]).one()
        assert booking.is_paid is False


def test_confirm_checkout_rejects_wrong_session_id(app, client, monkeypatch):
    body, _, _ = create_booking(client, monkeypatch)
    get_session = Mock()
    monkeypatch.setattr(
        "api.bookings.bookings_api.get_checkout_session",
        get_session,
    )

    response = confirm_checkout(
        client,
        body["reference"],
        {"checkoutSessionId": "cs_test_wrong_session"},
    )

    assert response.status_code == 422
    get_session.assert_not_called()
    with app.app_context():
        booking = Booking.query.filter_by(reference=body["reference"]).one()
        assert booking.is_paid is False


def test_confirm_checkout_rejects_session_from_another_booking(app, client, monkeypatch):
    first_body, first_session, _ = create_booking(client, monkeypatch)
    second_body, _, _ = create_booking(client, monkeypatch)
    get_session = Mock()
    monkeypatch.setattr(
        "api.bookings.bookings_api.get_checkout_session",
        get_session,
    )

    response = confirm_checkout(
        client,
        second_body["reference"],
        {"checkoutSessionId": first_session.id},
    )

    assert response.status_code == 422
    get_session.assert_not_called()
    with app.app_context():
        booking = Booking.query.filter_by(reference=second_body["reference"]).one()
        assert booking.is_paid is False


def test_confirm_checkout_rejects_mismatched_client_reference(app, client, monkeypatch):
    body, session, _ = create_booking(client, monkeypatch)
    monkeypatch.setattr(
        "api.bookings.bookings_api.get_checkout_session",
        lambda session_id: FakeStripeSession(
            session_id,
            payment_status="paid",
            client_reference_id="OTHERREF",
        ),
    )

    response = confirm_checkout(
        client,
        body["reference"],
        {"checkoutSessionId": session.id},
    )

    assert response.status_code == 422
    with app.app_context():
        booking = Booking.query.filter_by(reference=body["reference"]).one()
        assert booking.is_paid is False


def test_confirm_checkout_returns_502_when_stripe_fails(app, client, monkeypatch):
    body, session, _ = create_booking(client, monkeypatch)

    def failing_get_checkout_session(session_id):
        raise RuntimeError("Stripe is unavailable")

    monkeypatch.setattr(
        "api.bookings.bookings_api.get_checkout_session",
        failing_get_checkout_session,
    )

    response = confirm_checkout(
        client,
        body["reference"],
        {"checkoutSessionId": session.id},
    )

    assert response.status_code == 502
    with app.app_context():
        booking = Booking.query.filter_by(reference=body["reference"]).one()
        assert booking.is_paid is False


@pytest.mark.parametrize("payload", [
    {},
    [],
    {"checkoutSessionId": 123},
    {"checkoutSessionId": ""},
    {"confirmationSecret": "legacy-secret"},
])
def test_confirm_checkout_rejects_invalid_payloads(app, client, monkeypatch, payload):
    body, _, _ = create_booking(client, monkeypatch)
    get_session = Mock()
    monkeypatch.setattr(
        "api.bookings.bookings_api.get_checkout_session",
        get_session,
    )

    response = confirm_checkout(client, body["reference"], payload)

    assert response.status_code == 422
    get_session.assert_not_called()
    with app.app_context():
        booking = Booking.query.filter_by(reference=body["reference"]).one()
        assert booking.is_paid is False


def test_get_checkout_session_retrieves_from_stripe(monkeypatch):
    monkeypatch.setenv("STRIPE_API_KEY", "sk_test_fake")
    retrieve = Mock(return_value=FakeStripeSession("cs_test_123"))
    monkeypatch.setattr(
        "services.stripe.stripe.checkout.Session.retrieve",
        retrieve,
    )

    result = get_checkout_session("cs_test_123")

    retrieve.assert_called_once_with("cs_test_123")
    assert result.id == "cs_test_123"
