import os

from pwdlib import PasswordHash
from sqlmodel import Session, select

from app.db.models import FAQ, Role, TicketCategory, User
from app.db.session import engine

ROLES = {
    "CLIENTE": "Usuario cliente del prototipo.",
    "ASESOR": "Personal asesor del prototipo.",
    "SUPERVISOR": "Personal supervisor del prototipo.",
}
CATEGORIES = {
    "TARJETAS": "Consultas generales sobre tarjetas ficticias.",
    "CUENTAS": "Consultas generales sobre cuentas ficticias.",
    "CREDITOS": "Consultas generales sobre créditos ficticios.",
    "BANCA_DIGITAL": "Consultas generales sobre canales digitales ficticios.",
    "RECLAMOS_SIMPLES": "Orientación sobre incidencias simuladas.",
    "SOLICITUDES_INFORMACION": "Solicitudes de orientación general.",
}
DEMO_USERS = (
    ("cliente@demo.com", "Cliente Demo", "CLIENTE", "DEMO_CLIENT_PASSWORD"),
    ("asesor@demo.com", "Asesor Demo", "ASESOR", "DEMO_ADVISOR_PASSWORD"),
    (
        "supervisor@demo.com",
        "Supervisor Demo",
        "SUPERVISOR",
        "DEMO_SUPERVISOR_PASSWORD",
    ),
)
DEMO_FAQS = (
    (
        "TARJETAS",
        "¿Qué requisitos necesito para una tarjeta?",
        "En este prototipo puedes consultar requisitos generales de "
        "tarjetas ficticias.",
        "tarjeta,requisitos",
    ),
    (
        "BANCA_DIGITAL",
        "¿Cómo ingreso a la banca digital?",
        "En este prototipo, la banca digital es una funcionalidad simulada "
        "para fines académicos.",
        "banca,aplicacion,acceso",
    ),
)


password_hasher = PasswordHash.recommended()


def hash_password(password: str) -> str:
    return password_hasher.hash(password)


def seed_demo_data(session: Session) -> None:
    roles = {}
    for name, description in ROLES.items():
        role = session.exec(select(Role).where(Role.name == name)).first()
        if role is None:
            role = Role(name=name, description=description)
            session.add(role)
            session.flush()
        roles[name] = role

    categories = {}
    for name, description in CATEGORIES.items():
        if (
            session.exec(
                select(TicketCategory).where(TicketCategory.name == name)
            ).first()
            is None
        ):
            category = TicketCategory(name=name, description=description)
            session.add(category)
            session.flush()
        categories[name] = session.exec(
            select(TicketCategory).where(TicketCategory.name == name)
        ).one()

    session.flush()
    for email, full_name, role_name, password_env in DEMO_USERS:
        user = session.exec(select(User).where(User.email == email)).first()
        password = os.getenv(password_env, "demo-password-local")
        if user is None:
            session.add(
                User(
                    full_name=full_name,
                    email=email,
                    password_hash=hash_password(password),
                    role_id=roles[role_name].id,
                )
            )
        elif not user.password_hash.startswith("$argon2"):
            user.password_hash = hash_password(password)
    session.commit()
    supervisor = session.exec(
        select(User).where(User.email == "supervisor@demo.com")
    ).one()
    for category_name, question, answer, keywords in DEMO_FAQS:
        exists = session.exec(select(FAQ).where(FAQ.question == question)).first()
        if exists is None:
            session.add(
                FAQ(
                    category_id=categories[category_name].id,
                    question=question,
                    answer=answer,
                    keywords=keywords,
                    created_by=supervisor.id,
                )
            )
    session.commit()


def main() -> None:
    with Session(engine) as session:
        seed_demo_data(session)


if __name__ == "__main__":
    main()
