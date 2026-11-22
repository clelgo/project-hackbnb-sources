import hashlib
import random
import re
import secrets
import string
import time

import pytest
from flask import Flask

from api.bookings.bookings_api import bookings_api
from api.bookings.bookings_models import Booking
from api.rooms.rooms_models import Room
from api.users.users_models import User
from database.init import db


class FakeCheckoutSession:
    id = "cs_test_fake_session"
    url = "https://checkout.stripe.test/fake-session"


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

    def fake_create_checkout(**kwargs):
        captured.update(kwargs)
        return FakeCheckoutSession()

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

    return response.get_json(), captured["confirmation_secret"]


def test_confirmation_secret_is_random_and_not_timestamp_based(app, client, monkeypatch):
    body, secret = create_booking(client, monkeypatch)

    with app.app_context():
        booking = Booking.query.filter_by(reference=body["reference"]).one()
        stored_value = booking.checkout_confirmation_secret

    # The raw secret is only sent to Stripe; the database stores its hash.
    assert secret != stored_value
    assert stored_value == hashlib.sha256(secret.encode("utf-8")).hexdigest()
    assert len(secret) >= 32

    # Brute-forcing timestamps around the booking time must not recover the
    # secret. The previous implementation was sha512(str(int(time.time()))).
    now = int(time.time())
    timestamp_hashes = {
        hashlib.sha256(str(timestamp).encode("utf-8")).hexdigest()
        for timestamp in range(now - 3600, now + 3601)
    }
    timestamp_hashes |= {
        hashlib.sha512(str(timestamp).encode("utf-8")).hexdigest()
        for timestamp in range(now - 3600, now + 3601)
    }
    assert stored_value not in timestamp_hashes

    # Two bookings must never share the same secret.
    _, other_secret = create_booking(client, monkeypatch)
    assert other_secret != secret


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

    body, _ = create_booking(client, monkeypatch)

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


def test_confirm_checkout_rejects_wrong_secret(app, client, monkeypatch):
    body, _ = create_booking(client, monkeypatch)

    response = client.post(
        f"/bookings/{body['reference']}/confirm-checkout",
        json={"confirmationSecret": "wrong-secret"},
    )

    assert response.status_code == 422
    with app.app_context():
        booking = Booking.query.filter_by(reference=body["reference"]).one()
        assert booking.is_paid is False


def test_confirm_checkout_rejects_secret_hash_from_database(app, client, monkeypatch):
    body, _ = create_booking(client, monkeypatch)

    with app.app_context():
        booking = Booking.query.filter_by(reference=body["reference"]).one()
        stored_hash = booking.checkout_confirmation_secret

    response = client.post(
        f"/bookings/{body['reference']}/confirm-checkout",
        json={"confirmationSecret": stored_hash},
    )

    assert response.status_code == 422


def test_confirm_checkout_accepts_correct_secret(app, client, monkeypatch):
    body, secret = create_booking(client, monkeypatch)

    response = client.post(
        f"/bookings/{body['reference']}/confirm-checkout",
        json={"confirmationSecret": secret},
    )

    assert response.status_code == 200
    assert response.get_json() == {"success": True}
    with app.app_context():
        booking = Booking.query.filter_by(reference=body["reference"]).one()
        assert booking.is_paid is True


def test_confirm_checkout_rejects_missing_secret(app, client, monkeypatch):
    body, _ = create_booking(client, monkeypatch)

    response = client.post(f"/bookings/{body['reference']}/confirm-checkout", json={})

    assert response.status_code == 422
    with app.app_context():
        booking = Booking.query.filter_by(reference=body["reference"]).one()
        assert booking.is_paid is False


@pytest.mark.parametrize("payload", [
    "[]",
    '{"confirmationSecret": 123}',
    '{"confirmationSecret": ""}',
])
def test_confirm_checkout_rejects_malformed_payloads(app, client, monkeypatch, payload):
    body, _ = create_booking(client, monkeypatch)

    response = client.post(
        f"/bookings/{body['reference']}/confirm-checkout",
        data=payload,
        content_type="application/json",
    )

    assert response.status_code == 422
    with app.app_context():
        booking = Booking.query.filter_by(reference=body["reference"]).one()
        assert booking.is_paid is False
