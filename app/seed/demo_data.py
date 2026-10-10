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

KNOWLEDGE_VERIFIED_AT = datetime(2026, 10, 10, tzinfo=UTC)

DEMO_FAQS = (
    (
        "TARJETAS",
        "¿Qué requisitos necesito para solicitar una tarjeta?",
        (
            "Los requisitos dependen del tipo de tarjeta y de la evaluación del Banco. "
            "Revisa la información vigente de cada producto en el sitio oficial. "
            "Este asistente no evalúa solicitudes ni garantiza aprobaciones."
        ),
        "tarjeta,requisitos,solicitud,crédito,debito",
        "https://www.bancognb.com.pe/inicio/banca-personas/tarjetas",
    ),
    (
        "BANCA_DIGITAL",
        "¿Cómo ingreso a la banca digital?",
        (
            "Banco GNB ofrece Banca Móvil y Banca por Internet. Para afiliación, "
            "acceso "
            "o recuperación de contraseña, consulta las guías oficiales del canal. "
            "Nunca compartas aquí tu contraseña, token ni código de verificación."
        ),
        "banca,aplicacion,acceso,ingreso,clave,contraseña,afiliación",
        "https://www.bancognb.com.pe/inicio/canales/canales-digitales/banca-movil-personas",
    ),
    (
        "CUENTAS",
        "¿Cómo consulto el saldo de mi cuenta?",
        (
            "El Banco indica que puedes consultar saldos y movimientos mediante sus "
            "canales digitales o Banca por Teléfono. Este asistente no accede a tus "
            "cuentas ni puede mostrar saldos."
        ),
        "cuenta,saldo,movimientos,consulta,ahorros",
        "https://www.bancognb.com.pe/inicio/canales/banca-por-telefono",
    ),
    (
        "TARJETAS",
        "¿Qué hago si mi tarjeta está bloqueada?",
        (
            "Si necesitas bloquear una tarjeta o canal digital, la página oficial "
            "indica atención telefónica de bloqueos las 24 horas: Lima (01) 616-4722 "
            "o provincias 0801-00088, opción 0; también señala la línea de emergencia "
            "1820. Verifica los datos vigentes en la fuente oficial."
        ),
        "bloqueada,bloqueo,desbloqueo,perdida,robo,urgente",
        "https://www.bancognb.com.pe/inicio/servicio-cliente/reclamaciones-consulta-24hr",
    ),
    (
        "CREDITOS",
        "¿Cómo solicito información sobre un crédito?",
        (
            "Banco GNB publica información sobre sus préstamos personales y canales "
            "de solicitud. Las condiciones, requisitos y cualquier oferta están "
            "sujetos a evaluación y pueden cambiar; consulta la página oficial. Este "
            "asistente no cotiza ni aprueba créditos."
        ),
        "credito,crédito,prestamo,préstamo,cuota,requisitos,tasa",
        "https://www.bancognb.com.pe/inicio/banca-personas/prestamos/prestamo-personal",
    ),
    (
        "RECLAMOS_SIMPLES",
        "¿Cómo consulto el estado de un reclamo?",
        (
            "Para conocer el estado de un reclamo formal, utiliza los canales de "
            "atención oficiales y ten a mano tu código de seguimiento. El ticket de "
            "este portal no equivale a un reclamo formal ante el Banco."
        ),
        "reclamo,estado,seguimiento,incidencia,queja,código",
        "https://www.bancognb.com.pe/inicio/servicio-cliente/reclamaciones-consulta",
    ),
    (
        "CUENTAS",
        "¿Cómo solicito un estado de cuenta?",
        (
            "La Banca por Teléfono incluye la solicitud de duplicado de estado de "
            "cuenta. Comunícate con el Banco para confirmar requisitos y canales "
            "vigentes; no compartas aquí números de cuenta ni documentos personales."
        ),
        "estado,cuenta,documento,periodo,duplicado,extracto",
        "https://www.bancognb.com.pe/inicio/canales/banca-por-telefono",
    ),
    (
        "SOLICITUDES_INFORMACION",
        "¿Cómo puedo contactar a un asesor?",
        (
            "Para consultas, la página oficial publica Banca por Teléfono: (01) "
            "616-4722 en Lima y 0801-00088 en provincias, con horarios regulares "
            "indicados en el sitio. Los bloqueos tienen una ruta de atención distinta "
            "y disponible 24 horas. Confirma la información actual en la fuente "
            "oficial."
        ),
        "asesor,atencion,ayuda,telefono,contacto,horario",
        "https://www.bancognb.com.pe/inicio/canales/banca-por-telefono",
    ),
    (
        "TARJETAS",
        "¿Puedo habilitar o deshabilitar compras por internet en mi tarjeta?",
        (
            "La página oficial indica que los servicios adicionales de tarjetas, como "
            "compras por internet, pueden configurarse desde los canales digitales. "
            "Si no tienes acceso, consulta las opciones de atención publicadas por el "
            "Banco."
        ),
        "tarjeta,internet,compras,habilitar,deshabilitar,configurar,seguridad",
        "https://www.bancognb.com.pe/inicio/banca-personas/tarjetas",
    ),
    (
        "BANCA_DIGITAL",
        "¿Cómo me afilio a Banca Móvil?",
        (
            "La página oficial de Banca Móvil enlaza la guía de afiliación y registro "
            "para personas naturales. Sigue esa guía vigente; no compartas en este "
            "chat "
            "contraseñas, tokens, OTP ni códigos de seguridad."
        ),
        "banca movil,afiliar,afiliacion,registro,aplicacion,app,guia",
        "https://www.bancognb.com.pe/inicio/canales/canales-digitales/banca-movil-personas",
    ),
    (
        "BANCA_DIGITAL",
        "¿Qué hago si olvidé mi contraseña de Banca Móvil?",
        (
            "La sección oficial de Banca Móvil publica una guía de recuperación de "
            "contraseña. Utiliza esa guía o los canales oficiales; este asistente no "
            "puede restablecer credenciales ni debe recibirlas."
        ),
        "olvide,olvidé,contraseña,clave,recuperar,restablecer,banca movil",
        "https://www.bancognb.com.pe/inicio/canales/canales-digitales/banca-movil-personas",
    ),
    (
        "CUENTAS",
        "¿Qué necesito para abrir una Cuenta de Ahorro?",
        (
            "La página oficial de Cuenta de Ahorro publica los requisitos y documentos "
            "aplicables. Revísala antes de iniciar, porque montos y condiciones del "
            "producto pueden cambiar."
        ),
        "cuenta ahorro abrir requisitos documento dni soles dolares",
        "https://www.bancognb.com.pe/inicio/banca-personas/cuentas/cuenta-ahorro",
    ),
    (
        "CUENTAS",
        "¿Qué tipos de cuentas ofrece Banco GNB?",
        (
            "El sitio oficial presenta varias opciones de cuentas para personas, con "
            "características distintas. Compara sus condiciones vigentes directamente "
            "en la página del Banco antes de elegir."
        ),
        "tipos cuenta corriente sueldo cts ahorro plazo opciones",
        "https://www.bancognb.com.pe/inicio/banca-personas/cuentas",
    ),
    (
        "CREDITOS",
        "¿El asistente puede decirme si me aprobarán un préstamo?",
        (
            "No. La aprobación y las condiciones dependen de la evaluación que realiza "
            "el Banco. Consulta la información oficial del producto o sus canales de "
            "atención; no compartas datos financieros sensibles en este chat."
        ),
        "prestamo credito aprobacion evaluar oferta calificacion",
        "https://www.bancognb.com.pe/inicio/banca-personas/prestamos/prestamo-personal",
    ),
    (
        "RECLAMOS_SIMPLES",
        "¿Cuál es la diferencia entre un reclamo y un requerimiento?",
        (
            "Banco GNB define un reclamo como una manifestación de insatisfacción "
            "relacionada con un producto, servicio u operación; un requerimiento busca "
            "una acción, consulta o información del Banco. El ticket de este portal "
            "no reemplaza esos canales formales."
        ),
        "reclamo requerimiento diferencia consulta insatisfaccion solicitud",
        "https://www.bancognb.com.pe/inicio/servicio-cliente/reclamaciones-consulta",
    ),
    (
        "RECLAMOS_SIMPLES",
        "¿Cómo presento un reclamo formal a Banco GNB?",
        (
            "Utiliza el Libro de Reclamaciones y los canales publicados por Banco GNB. "
            "El formulario oficial indica qué datos y medio de respuesta se solicitan. "
            "No introduzcas documentos ni números de tarjeta en este portal."
        ),
        "presentar reclamo formal libro formulario queja canal",
        "https://www.bancognb.com.pe/inicio/servicio-cliente/reclamaciones",
    ),
    (
        "RECLAMOS_SIMPLES",
        "¿Cuánto tarda la respuesta de un reclamo formal?",
        (
            "La página oficial consultada indica un plazo de atención de hasta 15 días "
            "hábiles para reclamos y requerimientos, con posibles extensiones "
            "excepcionales comunicadas por el Banco. Verifica siempre la información "
            "vigente en la fuente oficial."
        ),
        "plazo tiempo respuesta reclamo requerimiento dias habiles demora",
        "https://www.bancognb.com.pe/inicio/servicio-cliente/reclamaciones-consulta",
    ),
    (
        "SOLICITUDES_INFORMACION",
        "¿Cuál es el horario de Banca por Teléfono?",
        (
            "La página oficial consultada indica atención regular de lunes a viernes "
            "de 9:00 a. m. a 6:30 p. m. y sábados de 9:00 a. m. a 1:00 p. m. Los "
            "bloqueos tienen atención 24 horas. Confirma horarios vigentes en el sitio "
            "oficial."
        ),
        "horario banca telefono lunes viernes sabado atencion",
        "https://www.bancognb.com.pe/inicio/canales/banca-por-telefono",
    ),
    (
        "TARJETAS",
        "¿Qué hago si no reconozco una operación con mi tarjeta?",
        (
            "Comunícate de inmediato con los canales oficiales para recibir "
            "orientación "
            "y reportar el caso. Banco GNB informa que ciertos reclamos por "
            "operaciones "
            "no reconocidas pueden requerir documentación adicional. No compartas aquí "
            "el número completo de tarjeta, claves ni códigos."
        ),
        "operacion no reconocida tarjeta fraude consumo desconocido reclamo",
        "https://www.bancognb.com.pe/inicio/servicio-cliente/reclamaciones",
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
                assigned_advisor_id=advisor.id if spec["advisor"] else None,
                assigned_at=(
                    created_at + timedelta(hours=4) if spec["advisor"] else None
                ),
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

        if spec["source"] == TicketSource.CHATBOT:
            _ensure_conversation(
                session,
                ticket,
                client.id,
                spec["days_ago"],
                spec["status"] == TicketStatus.CANCELADO,
            )

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

    _seed_faq_catalog(session, categories, users["SUPERVISOR"])

    session.flush()
    _clean_existing_visible_text(session)
    if include_operational_data:
        _seed_operational_data(session, categories, users)
    _ensure_existing_ticket_slas(session, users["SUPERVISOR"].id)
    session.commit()


def seed_chatbot_knowledge(session: Session) -> int:
    """Upsert only FAQ content, leaving users, tickets and conversations untouched."""
    required_categories = {item[0] for item in DEMO_FAQS}
    categories = {
        category.name: category
        for category in session.exec(
            select(TicketCategory).where(
                TicketCategory.name.in_(required_categories),
                TicketCategory.is_active.is_(True),
            )
        ).all()
    }
    if required_categories - categories.keys():
        missing = ", ".join(sorted(required_categories - categories.keys()))
        raise RuntimeError(
            f"No se puede sembrar el conocimiento: faltan categorías activas: {missing}"
        )
    supervisor = session.exec(
        select(User)
        .join(Role, User.role_id == Role.id)
        .where(Role.name == "SUPERVISOR", User.is_active.is_(True))
    ).first()
    if supervisor is None:
        raise RuntimeError(
            "No se puede sembrar el conocimiento: falta un usuario Supervisor activo"
        )
    count = _seed_faq_catalog(session, categories, supervisor)
    session.commit()
    return count


def _seed_faq_catalog(
    session: Session,
    categories: dict[str, TicketCategory],
    supervisor: User,
) -> int:
    for category_name, question, answer, keywords, source_url in DEMO_FAQS:
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
            "source_url": source_url,
            "source_verified_at": KNOWLEDGE_VERIFIED_AT,
            "status": FAQ.Status.PUBLISHED,
            "priority": 0,
            "display_order": 0,
            "is_active": True,
            "created_by": supervisor.id,
            "updated_by": supervisor.id,
        }
        created = faq is None
        if faq is None:
            faq = FAQ(**values)
            session.add(faq)
        else:
            content_changed = any(
                not _same_seed_value(getattr(faq, field), value)
                for field, value in values.items()
                if field not in {"created_by", "updated_by"}
            )
            for field, value in values.items():
                setattr(faq, field, value)
            if content_changed:
                faq.version += 1
                faq.updated_at = datetime.now(UTC)
                faq.updated_by = supervisor.id
                session.add(faq)
        session.flush()
        faq.published_at = faq.published_at or faq.created_at
        version_exists = session.exec(
            select(FAQVersion).where(
                FAQVersion.faq_id == faq.id,
                FAQVersion.version == faq.version,
            )
        ).first()
        if version_exists is None:
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
                    source_url=faq.source_url,
                    source_verified_at=faq.source_verified_at,
                    status=faq.status,
                    priority=faq.priority,
                    display_order=faq.display_order,
                    is_active=faq.is_active,
                    published_at=faq.published_at,
                    unpublished_at=faq.unpublished_at,
                    changed_by=supervisor.id,
                    action="SEEDED" if created else "SEEDED_UPDATE",
                    changed_at=faq.updated_at,
                )
            )
    return len(DEMO_FAQS)


def _same_seed_value(existing: object, expected: object) -> bool:
    if isinstance(existing, datetime) and isinstance(expected, datetime):
        normalized_existing = (
            existing.replace(tzinfo=UTC)
            if existing.tzinfo is None
            else existing.astimezone(UTC)
        )
        normalized_expected = (
            expected.replace(tzinfo=UTC)
            if expected.tzinfo is None
            else expected.astimezone(UTC)
        )
        return normalized_existing == normalized_expected
    return existing == expected


def main() -> None:
    with Session(engine) as session:
        seed_demo_data(session)


if __name__ == "__main__":
    main()
