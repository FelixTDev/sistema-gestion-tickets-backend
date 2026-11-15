"""Seed an isolated, SQLite-only chatbot demonstration dataset."""

import os
from unittest.mock import patch

from sqlmodel import Session, select

from app.seed import demo_data
from app.seed.demo_data import hash_password


def seed_chatbot_demo_data(session: Session) -> None:
    """Create six operational tickets and two isolated demo clients."""
    if session.get_bind().dialect.name != "sqlite":
        raise ValueError("El seed de demo del chatbot solo permite SQLite.")
    with patch.object(
        demo_data,
        "OPERATIONAL_TICKETS",
        demo_data.OPERATIONAL_TICKETS[:6],
    ):
        demo_data.seed_demo_data(session, include_operational_data=True)

    role = session.exec(
        select(demo_data.Role).where(demo_data.Role.name == "CLIENTE")
    ).one()
    second_client = session.exec(
        select(demo_data.User).where(demo_data.User.email == "cliente2@demo.com")
    ).first()
    password = os.getenv("DEMO_SECOND_CLIENT_PASSWORD", "demo-password-local")
    if second_client is None:
        second_client = demo_data.User(
            full_name="Cliente Demo Dos",
            email="cliente2@demo.com",
            password_hash=hash_password(password),
            role_id=role.id,
            email_verified=True,
        )
        session.add(second_client)
        session.flush()
    else:
        second_client.full_name = "Cliente Demo Dos"
        second_client.role_id = role.id
        second_client.is_active = True
        second_client.email_verified = True
        if not second_client.password_hash.startswith("$argon2"):
            second_client.password_hash = hash_password(password)

    if session.get(demo_data.UserPreference, second_client.id) is None:
        session.add(demo_data.UserPreference(user_id=second_client.id))

    closed_ticket = session.exec(
        select(demo_data.Ticket).where(
            demo_data.Ticket.tracking_code == "TCK-LOCAL-006"
        )
    ).one()
    closed_ticket.client_id = second_client.id
    session.add(closed_ticket)
    session.commit()
