"""Idempotent local data seed for the ticket management backend."""

import json
import os
import re
from datetime import UTC, datetime, timedelta

from pwdlib import PasswordHash
from sqlmodel import Session, select

from app.core.config import get_settings
from app.db.models import (
    FAQ,
    ChatMessage,
    Conversation,
    FAQVersion,
    Role,
    SlaPolicy,
    Ticket,
    TicketAssignment,
    TicketCategory,
    TicketComment,
    TicketHistory,
    TicketSla,
    User,
    UserPreference,
)
from app.db.session import engine
from app.modules.chatbot.models.conversation import ConversationStatus
from app.modules.chatbot.models.message import SenderType
from app.modules.tickets.models.sla import SlaStatus
from app.modules.tickets.models.ticket import TicketPriority, TicketSource, TicketStatus
from app.shared.text import normalize_text

ROLES = {
    "CLIENTE": "Usuario que solicita orientación y seguimiento de sus tickets.",
    "ASESOR": "Personal responsable de atender y resolver tickets asignados.",
    "SUPERVISOR": "Personal responsable de administrar la operación y sus reportes.",
}

# The final boolean controls the public active catalog. The inactive category is
# intentionally left without tickets so it can be used to validate administration
# screens without breaking existing ticket references.
CATEGORIES = {
    "TARJETAS": ("Orientación sobre tarjetas y operaciones relacionadas.", True),
    "CUENTAS": ("Orientación sobre cuentas, saldos y movimientos.", True),
    "CREDITOS": ("Orientación general sobre solicitudes y productos de crédito.", True),
    "BANCA_DIGITAL": ("Orientación sobre canales y acceso digital.", True),
    "RECLAMOS_SIMPLES": ("Orientación para incidencias y reclamos de atención.", True),
    "SOLICITUDES_INFORMACION": (
        "Solicitudes de información y orientación general.",
        True,
    ),
    "INFORMACION_GENERAL": ("Categoría reservada para información general.", False),
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
        "¿Qué requisitos necesito para solicitar una tarjeta?",
        (
            "Para solicitar una tarjeta necesitas identificarte, presentar información "
            "de contacto actualizada y cumplir la evaluación correspondiente. "
            "Un asesor "
            "puede orientarte sobre los requisitos aplicables a tu solicitud."
        ),
        "tarjeta,requisitos,solicitud",
    ),
    (
        "BANCA_DIGITAL",
        "¿Cómo ingreso a la banca digital?",
        (
            "Ingresa desde el canal digital autorizado con tus credenciales "
            "personales. Si no puedes acceder, verifica tus datos, utiliza la opción "
            "de recuperación "
            "disponible y solicita orientación si el inconveniente continúa."
        ),
        "banca,aplicacion,acceso,ingreso",
    ),
    (
        "CUENTAS",
        "¿Cómo consulto el saldo de mi cuenta?",
        (
            "Puedes consultar el saldo desde el canal digital autorizado o solicitar "
            "orientación en un canal de atención. Revisa siempre que la sesión "
            "corresponda "
            "a tu cuenta antes de confirmar cualquier operación."
        ),
        "cuenta,saldo,movimientos",
    ),
    (
        "TARJETAS",
        "¿Qué hago si mi tarjeta está bloqueada?",
        (
            "Verifica el motivo del bloqueo en el canal de atención disponible y sigue "
            "las instrucciones de desbloqueo. Si no reconoces la causa, solicita la "
            "revisión "
            "de un asesor de inmediato."
        ),
        "bloqueada,bloqueo,desbloqueo",
    ),
    (
        "CREDITOS",
        "¿Cómo solicito información sobre un crédito?",
        (
            "Indica el tipo de crédito y el propósito de tu consulta para recibir "
            "orientación sobre requisitos, evaluación y próximos pasos. La aprobación "
            "está sujeta a las "
            "condiciones vigentes."
        ),
        "credito,crédito",
    ),
    (
        "RECLAMOS_SIMPLES",
        "¿Cómo consulto el estado de un reclamo?",
        (
            "Ten a la mano el código de seguimiento de tu reclamo y consulta su estado "
            "en el canal de atención. Un asesor puede revisar los avances y las "
            "acciones pendientes."
        ),
        "reclamo,estado,seguimiento,incidencia",
    ),
    (
        "CUENTAS",
        "¿Cómo solicito un estado de cuenta?",
        (
            "Solicita el periodo que necesitas mediante el canal de atención "
            "disponible. El asesor confirmará la información requerida y el medio "
            "habilitado para entregarte "
            "el documento."
        ),
        "estado,cuenta,documento,periodo",
    ),
    (
        "SOLICITUDES_INFORMACION",
        "¿Cómo puedo contactar a un asesor?",
        (
            "Describe brevemente tu consulta y conserva el código de seguimiento. Un "
            "asesor revisará la solicitud y te informará los siguientes pasos por el "
            "canal habilitado."
        ),
        "asesor,atencion,ayuda",
    ),
)

LEGACY_FAQ_QUESTIONS = {
    "¿Qué requisitos necesito para solicitar una tarjeta?": (
        "¿Qué requisitos necesito para una tarjeta?"
    ),
}

OPERATIONAL_TICKETS = (
    {
        "code": "TCK-LOCAL-001",
        "category": "TARJETAS",
        "subject": "Consulta sobre requisitos de tarjeta",
        "description": (
            "La persona solicita orientación sobre los requisitos para una tarjeta."
        ),
        "priority": TicketPriority.BAJA,
        "status": TicketStatus.NUEVO,
        "source": TicketSource.MANUAL,
        "days_ago": 6,
        "advisor": False,
        "comments": (("CLIENTE", "Agradezco la orientación sobre los requisitos."),),
    },
    {
        "code": "TCK-LOCAL-002",
        "category": "CUENTAS",
        "subject": "Consulta de saldo de cuenta",
        "description": (
            "La persona solicita ayuda para consultar el saldo de su cuenta."
        ),
        "priority": TicketPriority.MEDIA,
        "status": TicketStatus.ASIGNADO,
        "source": TicketSource.MANUAL,
        "days_ago": 5,
        "advisor": True,
        "comments": (("CLIENTE", "Quedo atento a la confirmación de la consulta."),),
    },
    {
        "code": "TCK-LOCAL-003",
        "category": "CREDITOS",
        "subject": "Orientación sobre solicitud de crédito",
        "description": (
            "La persona necesita conocer los pasos para iniciar una solicitud de "
            "crédito."
        ),
        "priority": TicketPriority.ALTA,
        "status": TicketStatus.EN_PROCESO,
        "source": TicketSource.MANUAL,
        "days_ago": 4,
        "advisor": True,
        "comments": (
            ("ASESOR", "Estoy revisando los requisitos aplicables a la solicitud."),
        ),
    },
    {
        "code": "TCK-LOCAL-004",
        "category": "BANCA_DIGITAL",
        "subject": "Acceso a la banca digital",
        "description": (
            "La persona reporta dificultades para ingresar al canal digital."
        ),
        "priority": TicketPriority.URGENTE,
        "status": TicketStatus.PENDIENTE_CLIENTE,
        "source": TicketSource.MANUAL,
        "days_ago": 3,
        "advisor": True,
        "comments": (
            ("ASESOR", "Se solicitaron datos adicionales para continuar la revisión."),
            ("CLIENTE", "Enviaré la información solicitada por el canal indicado."),
        ),
    },
    {
        "code": "TCK-LOCAL-005",
        "category": "RECLAMOS_SIMPLES",
        "subject": "Consulta sobre estado de reclamo",
        "description": "La persona solicita seguimiento de una incidencia reportada.",
        "priority": TicketPriority.ALTA,
        "status": TicketStatus.RESUELTO,
        "source": TicketSource.CHATBOT,
        "days_ago": 10,
        "advisor": True,
        "comments": (
            ("ASESOR", "La revisión fue completada y se comunicó el resultado."),
        ),
    },
    {
        "code": "TCK-LOCAL-006",
        "category": "SOLICITUDES_INFORMACION",
        "subject": "Solicitud de orientación general",
        "description": (
            "La persona recibió orientación y confirmó la solución de su consulta."
        ),
        "priority": TicketPriority.MEDIA,
        "status": TicketStatus.CERRADO,
        "source": TicketSource.MANUAL,
        "days_ago": 14,
        "advisor": True,
        "comments": (
            ("SUPERVISOR", "La atención quedó cerrada luego de confirmar la solución."),
        ),
    },
    {
        "code": "TCK-LOCAL-007",
        "category": "TARJETAS",
        "subject": "Solicitud cancelada por la persona",
        "description": (
            "La persona solicitó cancelar la atención antes de iniciar la gestión."
        ),
        "priority": TicketPriority.URGENTE,
        "status": TicketStatus.CANCELADO,
        "source": TicketSource.CHATBOT,
        "days_ago": 8,
        "advisor": False,
        "comments": (
            ("SUPERVISOR", "La solicitud fue cancelada con el motivo registrado."),
        ),
    },
)

password_hasher = PasswordHash.recommended()


def hash_password(password: str) -> str:
    return password_hasher.hash(password)


def _clean_visible_text(value: str | None) -> str | None:
    if value is None:
        return None
    replacements = (
        (r"\bprototipo\b", "servicio"),
        (r"\bficticios\b", "disponibles"),
        (r"\bficticias\b", "disponibles"),
        (r"\bficticio\b", "disponible"),
        (r"\bficticia\b", "disponible"),
        (r"\bsimulados\b", "disponibles"),
        (r"\bsimuladas\b", "disponibles"),
        (r"\bsimulado\b", "disponible"),
        (r"\bsimulada\b", "disponible"),
        (r"\bacadémicos\b", "informativos"),
        (r"\bacadémicas\b", "informativas"),
        (r"\bacadémico\b", "informativo"),
        (r"\bacadémica\b", "informativa"),
        (r"\bdemo\b", "local"),
    )
    result = value
    for pattern, replacement in replacements:
        result = re.sub(pattern, replacement, result, flags=re.IGNORECASE)
    return result


def _clean_existing_visible_text(session: Session) -> None:
    for row in session.exec(select(Role)).all():
        row.description = _clean_visible_text(row.description) or row.description
    for row in session.exec(select(TicketCategory)).all():
        row.description = _clean_visible_text(row.description) or row.description
    for row in session.exec(select(FAQ)).all():
        row.question = _clean_visible_text(row.question) or row.question
        row.answer = _clean_visible_text(row.answer) or row.answer
        row.keywords = _clean_visible_text(row.keywords) or row.keywords
    for row in session.exec(select(Ticket)).all():
        row.subject = _clean_visible_text(row.subject) or row.subject
        row.description = _clean_visible_text(row.description) or row.description
    for row in session.exec(select(TicketComment)).all():
        row.content = _clean_visible_text(row.content) or row.content
    for row in session.exec(select(TicketHistory)).all():
        row.old_value = _clean_visible_text(row.old_value)
        row.new_value = _clean_visible_text(row.new_value)
        row.description = _clean_visible_text(row.description) or row.description
    for row in session.exec(select(ChatMessage)).all():
        row.content = _clean_visible_text(row.content) or row.content


def _status_path(status: TicketStatus) -> list[tuple[TicketStatus, TicketStatus]]:
    paths = {
        TicketStatus.NUEVO: [],
        TicketStatus.ASIGNADO: [(TicketStatus.NUEVO, TicketStatus.ASIGNADO)],
        TicketStatus.EN_PROCESO: [
            (TicketStatus.NUEVO, TicketStatus.ASIGNADO),
            (TicketStatus.ASIGNADO, TicketStatus.EN_PROCESO),
        ],
        TicketStatus.PENDIENTE_CLIENTE: [
            (TicketStatus.NUEVO, TicketStatus.ASIGNADO),
            (TicketStatus.ASIGNADO, TicketStatus.EN_PROCESO),
            (TicketStatus.EN_PROCESO, TicketStatus.PENDIENTE_CLIENTE),
        ],
        TicketStatus.RESUELTO: [
            (TicketStatus.NUEVO, TicketStatus.ASIGNADO),
            (TicketStatus.ASIGNADO, TicketStatus.EN_PROCESO),
            (TicketStatus.EN_PROCESO, TicketStatus.RESUELTO),
        ],
        TicketStatus.CERRADO: [
            (TicketStatus.NUEVO, TicketStatus.ASIGNADO),
            (TicketStatus.ASIGNADO, TicketStatus.EN_PROCESO),
            (TicketStatus.EN_PROCESO, TicketStatus.RESUELTO),
            (TicketStatus.RESUELTO, TicketStatus.CERRADO),
        ],
        TicketStatus.CANCELADO: [(TicketStatus.NUEVO, TicketStatus.CANCELADO)],
    }
    return paths[status]


def _ensure_history(
    session: Session,
    ticket: Ticket,
    actor_id: str,
    action: str,
    old_value: str | None,
    new_value: str | None,
    description: str,
    created_at: datetime,
) -> None:
    existing = session.exec(
        select(TicketHistory).where(
            TicketHistory.ticket_id == ticket.id,
            TicketHistory.action == action,
            TicketHistory.old_value == old_value,
            TicketHistory.new_value == new_value,
            TicketHistory.description == description,
        )
    ).first()
    if existing is None:
        session.add(
            TicketHistory(
                ticket_id=ticket.id,
                actor_id=actor_id,
                action=action,
                old_value=old_value,
                new_value=new_value,
                description=description,
                created_at=created_at,
            )
        )


def _ensure_conversation(
    session: Session,
    ticket: Ticket,
    client_id: str,
    days_ago: int,
    unresolved: bool,
) -> None:
    conversation = (
        session.get(Conversation, ticket.conversation_id)
        if ticket.conversation_id
        else None
    )
    created_at = datetime.now(UTC) - timedelta(days=days_ago)
    if conversation is None:
        conversation = Conversation(
            user_id=client_id,
            status=ConversationStatus.CLOSED,
            started_at=created_at,
            ended_at=created_at + timedelta(minutes=8),
        )
        session.add(conversation)
        session.flush()
        ticket.conversation_id = conversation.id
    message_content = (
        "Necesito orientación sobre el seguimiento de mi solicitud."
        if unresolved
        else "Quiero consultar el estado de mi reclamo."
    )
    response_content = (
        (
            "No encontré una respuesta confiable para tu consulta. Puedes continuar "
            "con la creación de un ticket para recibir atención personalizada."
        )
        if unresolved
        else "Revisaremos tu solicitud y te informaremos los siguientes pasos."
    )
    if not session.exec(
        select(ChatMessage).where(
            ChatMessage.conversation_id == conversation.id,
            ChatMessage.sender_type == SenderType.USER,
        )
    ).first():
        session.add(
            ChatMessage(
                conversation_id=conversation.id,
                sender_type=SenderType.USER,
                content=message_content,
                created_at=created_at + timedelta(minutes=1),
            )
        )
    if not session.exec(
        select(ChatMessage).where(
            ChatMessage.conversation_id == conversation.id,
            ChatMessage.sender_type == SenderType.BOT,
        )
    ).first():
        session.add(
            ChatMessage(
                conversation_id=conversation.id,
                sender_type=SenderType.BOT,
                content=response_content,
                intent=None if unresolved else "RECLAMO",
                confidence=0 if unresolved else 0.85,
                created_at=created_at + timedelta(minutes=2),
            )
        )


def _sla_policy_key(priority: TicketPriority) -> str:
    return f"priority={priority.value}|category=*|source=*"


def _ensure_sla_policies(session: Session) -> None:
    settings = get_settings()
    for priority in TicketPriority:
        values = settings.sla_policy_defaults.get(priority.value)
        if values is None:
            continue
        key = _sla_policy_key(priority)
        policy = session.exec(
            select(SlaPolicy).where(SlaPolicy.policy_key == key)
        ).first()
        now = datetime.now(UTC)
        if policy is None:
            session.add(
                SlaPolicy(
                    policy_key=key,
                    priority=priority,
                    first_response_seconds=values["first_response_seconds"],
                    resolution_seconds=values["resolution_seconds"],
                    warning_seconds=values["warning_seconds"],
                    timezone_name=settings.sla_default_timezone,
                    calendar_name=settings.sla_default_calendar,
                    created_at=now,
                    updated_at=now,
                )
            )
        else:
            policy.first_response_seconds = values["first_response_seconds"]
            policy.resolution_seconds = values["resolution_seconds"]
            policy.warning_seconds = values["warning_seconds"]
            policy.timezone_name = settings.sla_default_timezone
            policy.calendar_name = settings.sla_default_calendar
            policy.is_active = True
            policy.updated_at = now
    session.flush()


def _ensure_existing_ticket_slas(session: Session, actor_id: str) -> None:
    policies = {
        policy.priority: policy
        for policy in session.exec(
            select(SlaPolicy).where(
                SlaPolicy.is_active,
                SlaPolicy.category_id.is_(None),
                SlaPolicy.source.is_(None),
            )
        ).all()
        if policy.priority is not None
    }
    for ticket in session.exec(select(Ticket)).all():
        if (
            session.exec(
                select(TicketSla).where(TicketSla.ticket_id == ticket.id)
            ).first()
            is not None
        ):
            continue
        policy = policies.get(ticket.priority)
        if policy is None:
            continue
        started_at = ticket.created_at
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=UTC)
        resolved_at = ticket.resolved_at
        if resolved_at is not None and resolved_at.tzinfo is None:
            resolved_at = resolved_at.replace(tzinfo=UTC)
        sla = TicketSla(
            ticket_id=ticket.id,
            policy_id=policy.id,
            started_at=started_at,
            first_response_due_at=started_at
            + timedelta(seconds=policy.first_response_seconds),
            resolution_due_at=started_at + timedelta(seconds=policy.resolution_seconds),
            status=(
                SlaStatus.CANCELLED
                if ticket.status == TicketStatus.CANCELADO
                else SlaStatus.COMPLETED
                if ticket.status in {TicketStatus.RESUELTO, TicketStatus.CERRADO}
                else SlaStatus.ACTIVE
            ),
            resolved_at=resolved_at,
            completed_within_sla=(
                resolved_at is not None
                and resolved_at
                <= started_at + timedelta(seconds=policy.resolution_seconds)
            )
            if ticket.status in {TicketStatus.RESUELTO, TicketStatus.CERRADO}
            else None,
        )
        session.add(sla)


def _ensure_comment(
    session: Session,
    ticket: Ticket,
    author_id: str,
    content: str,
    created_at: datetime,
) -> None:
    if (
        session.exec(
            select(TicketComment).where(
                TicketComment.ticket_id == ticket.id,
                TicketComment.author_id == author_id,
                TicketComment.content == content,
            )
        ).first()
        is None
    ):
        session.add(
            TicketComment(
                ticket_id=ticket.id,
                author_id=author_id,
                content=content,
                created_at=created_at,
            )
        )
        _ensure_history(
            session,
            ticket,
            author_id,
            "COMMENT_ADDED",
            None,
            None,
            f"Comentario registrado: {content}",
            created_at,
        )


def _seed_operational_data(
    session: Session,
    categories: dict[str, TicketCategory],
    users: dict[str, User],
) -> None:
    client = users["CLIENTE"]
    advisor = users["ASESOR"]
    supervisor = users["SUPERVISOR"]
    for spec in OPERATIONAL_TICKETS:
        created_at = datetime.now(UTC) - timedelta(days=spec["days_ago"])
        ticket = session.exec(
            select(Ticket).where(Ticket.tracking_code == spec["code"])
        ).first()
        if ticket is None:
            ticket = Ticket(
                tracking_code=spec["code"],
                client_id=client.id,
                category_id=categories[spec["category"]].id,
                subject=spec["subject"],
                description=spec["description"],
                priority=spec["priority"],
                status=spec["status"],
                source=spec["source"],
                created_at=created_at,
            )
            session.add(ticket)
            session.flush()
        else:
            ticket.client_id = client.id
            ticket.category_id = categories[spec["category"]].id
            ticket.subject = spec["subject"]
            ticket.description = spec["description"]
            ticket.priority = spec["priority"]
            ticket.status = spec["status"]
            ticket.source = spec["source"]
            ticket.created_at = created_at

        if spec["source"] == TicketSource.CHATBOT:
            _ensure_conversation(
                session,
                ticket,
                client.id,
                spec["days_ago"],
                spec["status"] == TicketStatus.CANCELADO,
            )
        if spec["advisor"]:
            ticket.assigned_advisor_id = advisor.id
            ticket.assigned_at = created_at + timedelta(hours=4)
            if (
                session.exec(
                    select(TicketAssignment).where(
                        TicketAssignment.ticket_id == ticket.id,
                        TicketAssignment.advisor_id == advisor.id,
                        TicketAssignment.assigned_by == supervisor.id,
                        TicketAssignment.unassigned_at.is_(None),
                    )
                ).first()
                is None
            ):
                session.add(
                    TicketAssignment(
                        ticket_id=ticket.id,
                        advisor_id=advisor.id,
                        assigned_by=supervisor.id,
                        assigned_at=ticket.assigned_at,
                    )
                )
        else:
            ticket.assigned_advisor_id = None
            ticket.assigned_at = None

        ticket.resolved_at = (
            created_at + timedelta(days=2)
            if spec["status"] in {TicketStatus.RESUELTO, TicketStatus.CERRADO}
            else None
        )
        ticket.closed_at = (
            created_at + timedelta(days=3)
            if spec["status"] == TicketStatus.CERRADO
            else None
        )
        ticket.cancelled_at = (
            created_at + timedelta(days=1)
            if spec["status"] == TicketStatus.CANCELADO
            else None
        )

        _ensure_history(
            session,
            ticket,
            client.id,
            "CREATED",
            None,
            TicketStatus.NUEVO.value,
            "Ticket creado desde el canal de atención correspondiente.",
            created_at,
        )
        for index, (old_status, new_status) in enumerate(
            _status_path(spec["status"]), start=1
        ):
            actor_id = (
                supervisor.id if new_status == TicketStatus.ASIGNADO else advisor.id
            )
            _ensure_history(
                session,
                ticket,
                actor_id,
                "STATUS_CHANGED",
                old_status.value,
                new_status.value,
                f"Estado actualizado de {old_status.value} a {new_status.value}.",
                created_at + timedelta(hours=4 + index),
            )
        if spec["advisor"]:
            _ensure_history(
                session,
                ticket,
                supervisor.id,
                "ASSIGNED",
                None,
                advisor.id,
                "Ticket asignado al asesor responsable.",
                ticket.assigned_at,
            )
        if spec["status"] == TicketStatus.CANCELADO:
            _ensure_history(
                session,
                ticket,
                supervisor.id,
                "CANCELLED",
                TicketStatus.NUEVO.value,
                TicketStatus.CANCELADO.value,
                "Ticket cancelado a solicitud de la persona usuaria.",
                ticket.cancelled_at,
            )
        for index, (author_role, content) in enumerate(spec["comments"], start=1):
            author_id = {
                "CLIENTE": client.id,
                "ASESOR": advisor.id,
                "SUPERVISOR": supervisor.id,
            }[author_role]
            _ensure_comment(
                session,
                ticket,
                author_id,
                content,
                created_at + timedelta(hours=6 + index),
            )


def seed_demo_data(session: Session, include_operational_data: bool = True) -> None:
    roles: dict[str, Role] = {}
    for name, description in ROLES.items():
        role = session.exec(select(Role).where(Role.name == name)).first()
        if role is None:
            role = Role(name=name, description=description)
            session.add(role)
            session.flush()
        else:
            role.description = description
        roles[name] = role

    categories: dict[str, TicketCategory] = {}
    for name, (description, is_active) in CATEGORIES.items():
        category = session.exec(
            select(TicketCategory).where(TicketCategory.name == name)
        ).first()
        if category is None:
            category = TicketCategory(
                name=name, description=description, is_active=is_active
            )
            session.add(category)
            session.flush()
        else:
            category.description = description
            if (
                is_active
                or not session.exec(
                    select(Ticket).where(Ticket.category_id == category.id)
                ).first()
            ):
                category.is_active = is_active
        categories[name] = category

    _ensure_sla_policies(session)

    users: dict[str, User] = {}
    for email, full_name, role_name, password_env in DEMO_USERS:
        user = session.exec(select(User).where(User.email == email)).first()
        password = os.getenv(password_env, "demo-password-local")
        if user is None:
            user = User(
                full_name=full_name,
                email=email,
                password_hash=hash_password(password),
                role_id=roles[role_name].id,
                email_verified=True,
            )
            session.add(user)
            session.flush()
        else:
            user.full_name = full_name
            user.role_id = roles[role_name].id
            user.is_active = True
            user.email_verified = True
            if not user.password_hash.startswith("$argon2"):
                user.password_hash = hash_password(password)
        users[role_name] = user

    for user in users.values():
        if session.get(UserPreference, user.id) is None:
            session.add(UserPreference(user_id=user.id))
    session.flush()

    for category_name, question, answer, keywords in DEMO_FAQS:
        faq = session.exec(select(FAQ).where(FAQ.question == question)).first()
        legacy_faq = None
        if question in LEGACY_FAQ_QUESTIONS:
            legacy_faq = session.exec(
                select(FAQ).where(FAQ.question == LEGACY_FAQ_QUESTIONS[question])
            ).first()
            if faq is None:
                faq = legacy_faq
            elif legacy_faq is not None and legacy_faq.id != faq.id:
                legacy_faq.is_active = False
        values = {
            "category_id": categories[category_name].id,
            "title": question,
            "question": question,
            "answer": answer,
            "summary": answer[:1000],
            "keywords": keywords,
            "tags": json.dumps(
                [term.strip() for term in keywords.split(",") if term.strip()],
                ensure_ascii=False,
            ),
            "synonyms": "[]",
            "normalized_content": normalize_text(
                f"{question} {answer} {keywords} {categories[category_name].name}"
            ),
            "normalized_title": normalize_text(question),
            "normalized_question": normalize_text(question),
            "status": FAQ.Status.PUBLISHED,
            "priority": 0,
            "display_order": 0,
            "is_active": True,
            "created_by": users["SUPERVISOR"].id,
            "updated_by": users["SUPERVISOR"].id,
        }
        if faq is None:
            faq = FAQ(**values)
            session.add(faq)
        else:
            for field, value in values.items():
                setattr(faq, field, value)
        session.flush()
        faq.published_at = faq.published_at or faq.created_at
        if (
            session.exec(select(FAQVersion).where(FAQVersion.faq_id == faq.id)).first()
            is None
        ):
            session.add(
                FAQVersion(
                    faq_id=faq.id,
                    version=faq.version,
                    category_id=faq.category_id,
                    title=faq.title,
                    question=faq.question,
                    answer=faq.answer,
                    summary=faq.summary,
                    keywords=faq.keywords,
                    tags=faq.tags,
                    synonyms=faq.synonyms,
                    normalized_content=faq.normalized_content,
                    intent=faq.intent,
                    status=faq.status,
                    priority=faq.priority,
                    display_order=faq.display_order,
                    is_active=faq.is_active,
                    published_at=faq.published_at,
                    unpublished_at=faq.unpublished_at,
                    changed_by=faq.updated_by or faq.created_by,
                    action="SEEDED",
                    changed_at=faq.updated_at,
                )
            )

    session.flush()
    _clean_existing_visible_text(session)
    if include_operational_data:
        _seed_operational_data(session, categories, users)
    _ensure_existing_ticket_slas(session, users["SUPERVISOR"].id)
    session.commit()


def main() -> None:
    with Session(engine) as session:
        seed_demo_data(session)


if __name__ == "__main__":
    main()
