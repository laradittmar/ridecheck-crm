"""SB2A test fixture: the launch professional a booking now requires.

SB1/SB2A made "a designated, active launch professional exists" a precondition of every
occupying booking — the booking authority fails closed without one (MISSING_PROFESSIONAL).
Fixtures that book therefore designate a SYNTHETIC one. Never a real person's data.
"""
from __future__ import annotations


def ensure_launch_professional(db):
    from app.models import Profesional
    from app.services.booking_representation import LAUNCH_PROFESSIONAL_KEY
    existing = db.query(Profesional).filter(Profesional.is_launch_default.is_(True)).first()
    if existing is not None:
        return existing
    prof = Profesional(nombre="Fixture", apellido="SB2A-FIXTURE",
                       email="fixture@example.invalid", resource_key=LAUNCH_PROFESSIONAL_KEY,
                       is_launch_default=True, is_active=True)
    db.add(prof)
    db.flush()
    return prof
