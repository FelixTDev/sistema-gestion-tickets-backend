"""Create the minimum non-demo data required for a first production login."""

import argparse
from getpass import getpass

from pwdlib import PasswordHash
from sqlmodel import Session, select

from app.core.config import get_settings
from app.db.models import Role, TicketCategory, User, UserPreference
from app.db.session import engine
from app.modules.usuarios.schemas.auth import RegisterRequest
from app.seed.demo_data import (
    CATEGORIES,
    ROLES,
    _ensure_sla_policies,
    _seed_faq_catalog,
)

password_hasher = PasswordHash.recommended()


def bootstrap_production_data(
    session: Session, *, full_name: str, email: str, password: str
) -> tuple[str, int]:
    """Add catalogs, SLA policies, curated FAQs and exactly one operator account.

    This deliberately does not call the general demo seed and never creates
    client/advisor demo users, tickets, conversations, or comments.
    """
    if get_settings().app_env.casefold() != "production":
        raise RuntimeError("El bootstrap solo se permite con APP_ENV=production")

    request = RegisterRequest(
        full_name=full_name,
        email=email,
        password=password,
    )
    normalized_email = str(request.email).lower()
    existing_user = session.exec(
        select(User).where(User.email == normalized_email)
    ).first()
    if existing_user is not None:
        raise RuntimeError(
            "Ese correo ya existe; el bootstrap no cambia ni reemplaza cuentas"
        )

    roles: dict[str, Role] = {}
    for name, description in ROLES.items():
        role = session.exec(select(Role).where(Role.name == name)).first()
        if role is None:
            role = Role(name=name, description=description)
            session.add(role)
            session.flush()
        roles[name] = role

    categories: dict[str, TicketCategory] = {}
    for name, (description, is_active) in CATEGORIES.items():
        category = session.exec(
            select(TicketCategory).where(TicketCategory.name == name)
        ).first()
        if category is None:
            category = TicketCategory(
                name=name,
                description=description,
                is_active=is_active,
            )
            session.add(category)
            session.flush()
        categories[name] = category

    _ensure_sla_policies(session)
    supervisor = User(
        full_name=request.full_name.strip(),
        email=normalized_email,
        password_hash=password_hasher.hash(request.password),
        role_id=roles["SUPERVISOR"].id,
        email_verified=True,
    )
    session.add(supervisor)
    session.flush()
    session.add(UserPreference(user_id=supervisor.id))
    session.flush()
    faq_count = _seed_faq_catalog(session, categories, supervisor)
    session.commit()
    return supervisor.email, faq_count


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Bootstrap seguro de la primera cuenta Supervisor"
    )
    parser.add_argument("--email", required=True, help="Correo del Supervisor")
    parser.add_argument("--name", required=True, help="Nombre completo")
    args = parser.parse_args()

    if get_settings().app_env.casefold() != "production":
        parser.error("APP_ENV debe ser production")

    password = getpass("Contraseña nueva del Supervisor: ")
    confirmation = getpass("Repite la contraseña: ")
    if password != confirmation:
        parser.error("Las contraseñas no coinciden")

    with Session(engine) as session:
        email, faq_count = bootstrap_production_data(
            session,
            full_name=args.name,
            email=args.email,
            password=password,
        )

    print(
        f"Bootstrap completado para {email}; {faq_count} FAQs publicadas. "
        "No se crearon tickets ni usuarios demo."
    )


if __name__ == "__main__":
    main()
