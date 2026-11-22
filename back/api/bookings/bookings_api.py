import hmac
import secrets
import string
from datetime import date
from flask import jsonify, Blueprint, request

from database.init import db
from api.rooms.rooms_models import Room
from services.stripe import create_checkout, get_checkout_lines, get_checkout_session
from .bookings_models import Booking
from sqlalchemy.orm import joinedload

bookings_api = Blueprint('bookings', __name__)


@bookings_api.route('/<reference>', methods=['GET'])
def get_booking_details(reference: str):
    """
    Get booking details
    """

    booking = Booking.query.options(joinedload(Booking.room)).filter_by(reference=reference).first()
    if not booking:
        return {'error': 'Booking not found'}, 404

    return jsonify(booking.to_dict())


@bookings_api.route('/<reference>/checkout-lines', methods=['GET'])
def get_booking_checkout_lines(reference: str):
    """
    Get booking checkout lines
    """

    booking = Booking.query.options(joinedload(Booking.room)).filter_by(reference=reference).first()
    if not booking:
        return {'error': 'Booking not found'}, 404
    
    if not booking.is_paid:
        return {'error': 'Cannot get checkout lines without payment'}, 422

    if not booking.stripe_checkout_session_id:
        return {'error': 'Missing checkout session'}, 422

    try:
        # Never use a session ID supplied by the client: only the one created
        # for this booking is allowed.
        return jsonify(get_checkout_lines(booking.stripe_checkout_session_id))
    except Exception:
        return {'error': 'Unable to get checkout lines'}, 502


@bookings_api.route('/', methods=['POST'])
def new_booking():
    """
    Register a new booking for a room
    """

    # Check room exists
    room = Room.query.filter_by(id=request.json["roomId"]).first()
    if not room:
        return {'error': 'Room not found'}, 404

    # Check arrival date is format YYYY-MM-DD
    try:
        arrival_date = date.fromisoformat(request.json["arrivalDate"])
    except:
        return {'error': 'Invalid arrival date'}, 400

    # Check departure date is format YYYY-MM-DD
    try:
        departure_date = date.fromisoformat(request.json["departureDate"])
    except:
        return {'error': 'Invalid departure date'}, 400
    
    # Check departure date is later than arrival date
    if departure_date < arrival_date:
        return {'error': 'Invalid departure date'}, 400

    # Check guests count is valid and < 10 guests
    guests_count = request.json["guestsCount"]
    if not isinstance(guests_count, int) or guests_count < 1 or guests_count > 10:
        return {'error': 'Invalid guests count'}, 400
    
    # Determine how many days is the booking for
    booking_duration_in_days = (departure_date - arrival_date).days
    if booking_duration_in_days < 1:
        return {'error': 'Invalid booking duration'}, 400
    
    # Determine the booking total price
    booking_price = room.price * booking_duration_in_days

    # Generate an unpredictable booking reference using a CSPRNG.
    # The reference is a public identifier, but it must not be guessable from
    # previous references (Mersenne Twister output is predictable).
    booking_reference = ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(8))

    # Create Stripe checkout session
    checkout_session = create_checkout(
        reference=booking_reference,
        name=f"{booking_duration_in_days} nuit{'s' if booking_duration_in_days > 1 else ''} à {room.title} ({room.city})",
        unit_price=room.price,
        quantity=booking_duration_in_days,
    )

    # Create booking
    db.session.add(Booking(
        reference=booking_reference,
        arrival_date=request.json["arrivalDate"],
        departure_date=request.json["departureDate"],
        guests_count=guests_count,
        total_price=booking_price,
        stripe_checkout_session_id=checkout_session.id,
        is_paid=False,
        room_id=room.id,
    ))
    db.session.commit()

    return jsonify({
        "reference": booking_reference,
        "duration_in_days": booking_duration_in_days,
        "price": booking_price,
        "checkout_url": checkout_session.url,
    })


@bookings_api.route('/<reference>/confirm-checkout', methods=['POST'])
def confirm_booking_checkout(reference: str):
    """
    Confirm checkout payment for a booking
    """

    booking = Booking.query.filter_by(reference=reference).first()
    if not booking:
        return {'error': 'Booking not found'}, 404
    
    payload = request.get_json(silent=True)
    checkout_session_id = payload.get("checkoutSessionId") if isinstance(payload, dict) else None
    if not isinstance(checkout_session_id, str) or not checkout_session_id:
        return {'error': 'Invalid checkout session ID'}, 422

    # The session ID must be the one created for this booking.
    if not booking.stripe_checkout_session_id or not hmac.compare_digest(
        booking.stripe_checkout_session_id, checkout_session_id
    ):
        return {'error': 'Invalid checkout session ID'}, 422

    # Never trust the client: ask Stripe whether the session was actually paid.
    try:
        checkout_session = get_checkout_session(checkout_session_id)
    except Exception:
        return {'error': 'Unable to verify checkout session'}, 502

    if getattr(checkout_session, 'payment_status', None) != 'paid':
        return {'error': 'Payment not completed'}, 422

    if getattr(checkout_session, 'client_reference_id', None) != booking.reference:
        return {'error': 'Checkout session does not match booking'}, 422

    booking.is_paid = True
    db.session.commit()

    return jsonify({
        "success": True,
    })
