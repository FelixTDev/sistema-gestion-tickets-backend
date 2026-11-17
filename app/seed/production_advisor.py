"""Provision one advisor account in an already initialized production database."""

import argparse
from getpass import getpass

from pwdlib import PasswordHash
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.core.config import get_settings
from app.db.models import Role, User, UserPreference
from app.db.session import engine
from app.modules.auditoria.services.audit_service import AuditService
from app.modules.usuarios.schemas.auth import RegisterRequest

password_hasher = PasswordHash.recommended()


def create_production_advisor(
    session: Session,
    *,
    full_name: str,
    email: str,
    password: str,
    email_confirmed: bool,
) -> User:
    """Create one ASESOR without changing catalogs or existing accounts."""
    if get_settings().app_env.casefold() != "production":
        raise RuntimeError("El alta de asesores solo se permite con APP_ENV=production")
    if not email_confirmed:
        raise RuntimeError("Confirma que tienes acceso al correo del asesor")

    request = RegisterRequest(
        full_name=full_name.strip(),
        email=email,
        password=password,
    )
    normalized_email = str(request.email).lower()
    existing_user = session.exec(
        select(User).where(User.email == normalized_email)
    ).first()
    if existing_user is not None:
        raise RuntimeError("Ese correo ya existe; no se modificó ninguna cuenta")

    advisor_role = session.exec(select(Role).where(Role.name == "ASESOR")).first()
    if advisor_role is None:
        raise RuntimeError("El rol ASESOR no está configurado")

    advisor = User(
        full_name=request.full_name,
        email=normalized_email,
        password_hash=password_hasher.hash(request.password),
        phone=request.phone,
        role_id=advisor_role.id,
        email_verified=True,
    )
    try:
        session.add(advisor)
        session.flush()
        session.add(UserPreference(user_id=advisor.id))
        AuditService().record(
            session,
            event_type="SECURITY",
            action="ADVISOR_ACCOUNT_CREATED",
            actor_user_id=None,
            actor_role=None,
            resource_type="USER",
            resource_id=advisor.id,
            target_user_id=advisor.id,
            success=True,
            after_data={"role": "ASESOR", "email_verified": True},
            metadata={"provisioning_method": "production_cli"},
        )
        session.commit()
        session.refresh(advisor)
    except IntegrityError as error:
        session.rollback()
        raise RuntimeError(
            "No se pudo crear el asesor; verifica si el correo ya está registrado"
        ) from error

    return advisor


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Crear una cuenta Asesor en la base de producción"
    )
    parser.add_argument(
        "--email", required=True, help="Correo controlado por el asesor"
    )
    parser.add_argument("--name", required=True, help="Nombre completo del asesor")
    args = parser.parse_args()

    if get_settings().app_env.casefold() != "production":
        parser.error("APP_ENV debe ser production")

    confirmation = input(f"Confirma que puedes recibir correo en {args.email} [s/N]: ")
    if confirmation.strip().casefold() not in {"s", "si", "sí"}:
        parser.error("No se confirmó el acceso al correo; no se creó la cuenta")

    password = getpass("Contraseña temporal del asesor: ")
    password_confirmation = getpass("Repite la contraseña: ")
    if password != password_confirmation:
        parser.error("Las contraseñas no coinciden; no se creó la cuenta")

    try:
        with Session(engine) as session:
            advisor = create_production_advisor(
                session,
                full_name=args.name,
                email=args.email,
                password=password,
                email_confirmed=True,
            )
    except ValidationError:
        parser.error(
            "Datos inválidos; revisa el correo, el nombre y la política de contraseña"
        )
    except RuntimeError as error:
        parser.error(str(error))

    print(
        f"Cuenta ASESOR creada para {advisor.email}; no se modificaron otras cuentas."
    )


if __name__ == "__main__":
    main()
