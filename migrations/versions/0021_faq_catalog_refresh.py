"""Refresh the published demonstration FAQ catalog with reviewed answers.

Rows are located by their stable question text and category name. Existing FAQ
version snapshots remain untouched; each content change appends a new snapshot.
"""

import json
import re
import unicodedata
from datetime import UTC, datetime
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision = "0021_faq_catalog_refresh"
down_revision = "0020_faq_content_cleanup"
branch_labels = None
depends_on = None

_SBS_REGULATION_URL = (
    "https://intranet2.sbs.gob.pe/dv_int_cn/2230/v6.0/Adjuntos/4036-2022.R%20.pdf"
)
_SBS_REGULATION_VERIFIED_AT = datetime(2026, 11, 17, tzinfo=UTC)

_FAQ_UPDATES = (
    {
        "category": "TARJETAS",
        "question": "¿Qué requisitos necesito para solicitar una tarjeta?",
        "legacy_questions": ("¿Qué requisitos necesito para una tarjeta?",),
        "answer": (
            "Los requisitos dependen del emisor y del producto. Antes de solicitar "
            "una tarjeta, revisa las condiciones, los costos y los criterios de "
            "evaluación publicados por quien la ofrece. Este prototipo no emite "
            "tarjetas ni evalúa solicitudes."
        ),
        "keywords": (
            "tarjeta,requisitos,solicitud,solicitar,emisor,producto,condiciones,"
            "costos,evaluación"
        ),
    },
    {
        "category": "BANCA_DIGITAL",
        "question": "¿Cómo ingreso al portal de solicitudes?",
        "legacy_questions": ("¿Cómo ingreso a la banca digital?",),
        "answer": (
            "Para ingresar a este portal, selecciona Ingresar y usa la cuenta "
            "registrada. Si te refieres a la banca digital o aplicación de otra "
            "entidad, este proyecto no administra ese acceso; consulta al proveedor "
            "por un canal oficial. No compartas contraseñas ni códigos en el chat."
        ),
        "keywords": (
            "portal,solicitudes,ingresar,acceso,cuenta,banca,digital,aplicación,"
            "afiliación"
        ),
    },
    {
        "category": "CUENTAS",
        "question": "¿Cómo consulto el saldo de mi cuenta?",
        "legacy_questions": (),
        "answer": (
            "Este prototipo no está conectado a cuentas y no puede consultar saldos "
            "ni movimientos. Para una cuenta real, utiliza los canales oficiales de "
            "su proveedor. No compartas aquí credenciales ni datos financieros."
        ),
        "keywords": "cuenta,saldo,movimientos,consulta,consultar,ver",
    },
    {
        "category": "TARJETAS",
        "question": "¿Qué hago si mi tarjeta está bloqueada?",
        "legacy_questions": (),
        "answer": (
            "Este prototipo no puede bloquear ni desbloquear tarjetas. Si se trata "
            "de una tarjeta real, comunícate con su emisor por un canal oficial para "
            "que te indique cómo proceder. No compartas aquí el número completo, "
            "claves ni códigos."
        ),
        "keywords": "tarjeta,bloqueada,bloqueo,desbloqueo,perdida,robo",
    },
    {
        "category": "CREDITOS",
        "question": "¿Cómo solicito información sobre un crédito?",
        "legacy_questions": (),
        "answer": (
            "Este prototipo no ofrece ni cotiza créditos. Para revisar una oferta "
            "real, consulta las condiciones y costos publicados por quien ofrece el "
            "producto. No compartas aquí documentos ni información financiera "
            "sensible."
        ),
        "keywords": "credito,crédito,prestamo,préstamo,oferta,condiciones,costos",
    },
    {
        "category": "RECLAMOS_SIMPLES",
        "question": "¿Cómo consulto el estado de un reclamo?",
        "legacy_questions": (),
        "answer": (
            "Este portal solo muestra las solicitudes creadas aquí; no consulta "
            "reclamos formales registrados ante otras entidades. Para revisar una "
            "solicitud del portal, entra en Mis solicitudes. Para un reclamo formal, "
            "consulta el canal donde lo registraste y utiliza su código de "
            "seguimiento."
        ),
        "keywords": "reclamo,estado,seguimiento,incidencia,queja,código,solicitud",
    },
    {
        "category": "CUENTAS",
        "question": "¿Cómo solicito un estado de cuenta?",
        "legacy_questions": (),
        "answer": (
            "Este prototipo no accede a cuentas ni emite estados de cuenta. "
            "Solicítalo al proveedor de tu cuenta mediante sus canales oficiales. "
            "No envíes aquí documentos ni datos financieros reales."
        ),
        "keywords": "estado,cuenta,documento,periodo,duplicado,extracto",
    },
    {
        "category": "SOLICITUDES_INFORMACION",
        "question": "¿Cómo puedo contactar a un asesor?",
        "legacy_questions": (),
        "answer": (
            "No hay atención humana en vivo desde este chat. Si quieres que revisen "
            "tu caso, puedes preparar una solicitud, revisar sus datos y confirmar "
            "su creación. Luego podrás seguirla en Mis solicitudes."
        ),
        "keywords": "asesor,atencion,ayuda,telefono,contacto",
    },
    {
        "category": "TARJETAS",
        "question": (
            "¿Puedo habilitar o deshabilitar compras por internet en mi tarjeta?"
        ),
        "legacy_questions": (),
        "answer": (
            "Este prototipo no cambia la configuración de tarjetas. Para una tarjeta "
            "real, consulta con su emisor por un canal oficial qué controles están "
            "disponibles. No compartas aquí datos de la tarjeta ni códigos de "
            "seguridad."
        ),
        "keywords": (
            "tarjeta,internet,compras,habilitar,deshabilitar,configurar,seguridad"
        ),
    },
    {
        "category": "BANCA_DIGITAL",
        "question": "¿Qué hago si olvidé mi contraseña del portal?",
        "legacy_questions": ("¿Qué hago si olvidé mi contraseña de Banca Móvil?",),
        "answer": (
            "Este asistente no puede ver ni restablecer tu contraseña. Para el portal, "
            "usa las opciones de recuperación disponibles en la pantalla de acceso, "
            "si están habilitadas. Si te refieres a la aplicación de otra entidad, "
            "este prototipo no administra ese acceso. No escribas contraseñas ni "
            "códigos en el chat."
        ),
        "keywords": (
            "contraseña,clave,olvidé,recuperar,restablecer,portal,acceso,"
            "aplicación,banca móvil"
        ),
    },
    {
        "category": "CUENTAS",
        "question": "¿Qué necesito para abrir una cuenta de ahorro?",
        "legacy_questions": ("¿Qué necesito para abrir una Cuenta de Ahorro?",),
        "answer": (
            "Los requisitos y condiciones dependen del proveedor y del tipo de "
            "cuenta. Este prototipo no abre cuentas; consulta las condiciones "
            "publicadas por quien ofrece el producto."
        ),
        "keywords": "cuenta ahorro abrir requisitos proveedor condiciones",
    },
    {
        "category": "CUENTAS",
        "question": "¿Qué tipos de cuentas ofrece una entidad financiera?",
        "legacy_questions": ("¿Qué tipos de cuentas ofrece Banco GNB?",),
        "answer": (
            "Existen distintos tipos de cuentas y depósitos. Su disponibilidad, "
            "costos y condiciones dependen del producto y del proveedor. Este "
            "prototipo no ofrece cuentas."
        ),
        "keywords": (
            "tipos,cuentas,depósitos,disponibilidad,costos,condiciones,proveedor"
        ),
    },
    {
        "category": "CREDITOS",
        "question": "¿El asistente puede decirme si me aprobarán un préstamo?",
        "legacy_questions": (),
        "answer": (
            "No. Este asistente no evalúa solicitudes ni puede anticipar una "
            "aprobación. La evaluación corresponde a quien ofrece el préstamo. No "
            "compartas aquí datos financieros sensibles."
        ),
        "keywords": "préstamo,crédito,aprobación,evaluar,anticipar,evaluación",
    },
    {
        "category": "RECLAMOS_SIMPLES",
        "question": "¿Cuál es la diferencia entre un reclamo y un requerimiento?",
        "legacy_questions": (),
        "answer": (
            "En términos generales, un reclamo expresa insatisfacción con una "
            "operación, producto o servicio recibido; un requerimiento solicita "
            "información o una acción. Este portal académico registra tickets y no "
            "tramita reclamos formales."
        ),
        "keywords": (
            "reclamo,requerimiento,diferencia,consulta,información,acción,"
            "insatisfacción"
        ),
        "source_url": _SBS_REGULATION_URL,
        "source_verified_at": _SBS_REGULATION_VERIFIED_AT,
    },
    {
        "category": "RECLAMOS_SIMPLES",
        "question": "¿Cómo presento un reclamo formal ante una entidad financiera?",
        "legacy_questions": ("¿Cómo presento un reclamo formal a Banco GNB?",),
        "answer": (
            "Este portal no presenta reclamos formales: solo permite registrar "
            "solicitudes de demostración. Para un reclamo real, sigue el "
            "procedimiento oficial del proveedor correspondiente. No envíes "
            "documentos ni datos sensibles por este chat."
        ),
        "keywords": "presentar,reclamo,formal,formulario,queja,canal",
    },
    {
        "category": "RECLAMOS_SIMPLES",
        "question": "¿Cuánto tarda la respuesta de un reclamo formal?",
        "legacy_questions": (),
        "answer": (
            "Este prototipo no tramita reclamos formales ni puede consultar sus "
            "plazos. El plazo aplicable depende del procedimiento correspondiente; "
            "confírmalo con el canal oficial donde registraste el reclamo. Las "
            "solicitudes de este portal se siguen desde Mis solicitudes."
        ),
        "keywords": (
            "plazo,tiempo,respuesta,reclamo,requerimiento,días,hábiles,demora"
        ),
    },
    {
        "category": "SOLICITUDES_INFORMACION",
        "question": "¿El portal ofrece atención telefónica?",
        "legacy_questions": ("¿Cuál es el horario de Banca por Teléfono?",),
        "answer": (
            "Este prototipo no brinda atención telefónica ni transferencia en vivo. "
            "No confirma horarios de otros proveedores. Puedes usar el asistente o "
            "registrar una solicitud."
        ),
        "keywords": "horario,teléfono,banca,canal,atención,llamada,transferencia",
    },
    {
        "category": "TARJETAS",
        "question": "¿Qué hago si no reconozco una operación con mi tarjeta?",
        "legacy_questions": (),
        "answer": (
            "Si ves una operación que no reconoces, comunícate cuanto antes con el "
            "emisor de tu tarjeta por un canal oficial y sigue sus indicaciones. "
            "Este prototipo no está conectado a movimientos ni puede gestionar la "
            "tarjeta. No compartas aquí números completos, claves ni códigos."
        ),
        "keywords": ("operación,no,reconocida,tarjeta,fraude,consumo,cargo,emisor"),
    },
)

_RETIRED_FAQS = (("BANCA_DIGITAL", "¿Cómo me afilio a Banca Móvil?"),)


def _normalize(value: str) -> str:
    without_accents = "".join(
        char
        for char in unicodedata.normalize("NFD", value)
        if unicodedata.category(char) != "Mn"
    )
    return re.sub(r"\s+", " ", without_accents).strip().casefold()


def _tags(keywords: str) -> str:
    return json.dumps(
        [term.strip() for term in keywords.split(",") if term.strip()],
        ensure_ascii=False,
    )


def _faq_values(*, category_name: str, faq: object) -> dict[str, object]:
    question = str(faq["question"])
    answer = str(faq["answer"])
    keywords = str(faq["keywords"])
    return {
        "title": question,
        "question": question,
        "answer": answer,
        "summary": answer[:1000],
        "keywords": keywords,
        "tags": _tags(keywords),
        "normalized_content": _normalize(
            f"{question} {answer} {keywords} {category_name}"
        ),
        "normalized_title": _normalize(question),
        "normalized_question": _normalize(question),
        "intent": None,
        "source_url": faq.get("source_url"),
        "source_verified_at": faq.get("source_verified_at"),
    }


def _utc(value: object) -> datetime | None:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _same(left: object, right: object) -> bool:
    left_datetime = _utc(left)
    right_datetime = _utc(right)
    if left_datetime is not None or right_datetime is not None:
        return (
            left_datetime is not None
            and right_datetime is not None
            and left_datetime == right_datetime
        )
    return left == right


def _insert_version(
    connection,
    *,
    faq: object,
    values: dict[str, object],
    version: int,
    changed_by: str,
    action: str,
    changed_at: datetime,
    status: str,
    is_active: bool,
    published_at: datetime | None,
    unpublished_at: datetime | None,
) -> None:
    connection.execute(
        sa.text(
            """
            INSERT INTO faq_versions (
                id, faq_id, version, category_id, title, question, answer, summary,
                keywords, tags, synonyms, normalized_content, intent, source_url,
                source_verified_at, status, priority, display_order, is_active,
                published_at, unpublished_at, changed_by, action, changed_at
            ) VALUES (
                :id, :faq_id, :version, :category_id, :title, :question, :answer,
                :summary, :keywords, :tags, :synonyms, :normalized_content, :intent,
                :source_url, :source_verified_at, :status, :priority, :display_order,
                :is_active, :published_at, :unpublished_at, :changed_by, :action,
                :changed_at
            )
            """
        ),
        {
            "id": str(uuid4()),
            "faq_id": faq["id"],
            "version": version,
            "category_id": faq["category_id"],
            "title": values["title"],
            "question": values["question"],
            "answer": values["answer"],
            "summary": values["summary"],
            "keywords": values["keywords"],
            "tags": values["tags"],
            "synonyms": faq["synonyms"],
            "normalized_content": values["normalized_content"],
            "intent": values["intent"],
            "source_url": values["source_url"],
            "source_verified_at": values["source_verified_at"],
            "status": status,
            "priority": faq["priority"],
            "display_order": faq["display_order"],
            "is_active": is_active,
            "published_at": published_at,
            "unpublished_at": unpublished_at,
            "changed_by": changed_by,
            "action": action,
            "changed_at": changed_at,
        },
    )


def _update_faq(connection, *, faq: object, values: dict[str, object]) -> None:
    mutable_fields = (
        "title",
        "question",
        "answer",
        "summary",
        "keywords",
        "tags",
        "normalized_content",
        "normalized_title",
        "normalized_question",
        "intent",
        "source_url",
        "source_verified_at",
    )
    if all(_same(faq[field], values[field]) for field in mutable_fields):
        return

    now = datetime.now(UTC)
    next_version = int(faq["version"]) + 1
    changed_by = faq["updated_by"] or faq["created_by"]
    assignments = ", ".join(f"{field} = :{field}" for field in mutable_fields)
    connection.execute(
        sa.text(
            f"""
            UPDATE faqs
            SET {assignments}, version=:version, updated_at=:updated_at,
                updated_by=:updated_by
            WHERE id=:id
            """
        ),
        {
            **{field: values[field] for field in mutable_fields},
            "version": next_version,
            "updated_at": now,
            "updated_by": changed_by,
            "id": faq["id"],
        },
    )
    _insert_version(
        connection,
        faq=faq,
        values=values,
        version=next_version,
        changed_by=changed_by,
        action="MIGRATED_CONTENT_UPDATE",
        changed_at=now,
        status=str(faq["status"]),
        is_active=bool(faq["is_active"]),
        published_at=faq["published_at"],
        unpublished_at=faq["unpublished_at"],
    )


def _archive_faq(connection, *, faq: object) -> None:
    if str(faq["status"]) == "ARCHIVED" and not bool(faq["is_active"]):
        return
    now = datetime.now(UTC)
    next_version = int(faq["version"]) + 1
    changed_by = faq["updated_by"] or faq["created_by"]
    connection.execute(
        sa.text(
            """
            UPDATE faqs
            SET status='ARCHIVED', is_active=0, unpublished_at=:unpublished_at,
                version=:version, updated_at=:updated_at, updated_by=:updated_by
            WHERE id=:id
            """
        ),
        {
            "unpublished_at": now,
            "version": next_version,
            "updated_at": now,
            "updated_by": changed_by,
            "id": faq["id"],
        },
    )
    archived_values = {
        field: faq[field]
        for field in (
            "title",
            "question",
            "answer",
            "summary",
            "keywords",
            "tags",
            "normalized_content",
            "intent",
            "source_url",
            "source_verified_at",
        )
    }
    _insert_version(
        connection,
        faq=faq,
        values=archived_values,
        version=next_version,
        changed_by=changed_by,
        action="MIGRATED_ARCHIVE",
        changed_at=now,
        status="ARCHIVED",
        is_active=False,
        published_at=faq["published_at"],
        unpublished_at=now,
    )


def upgrade() -> None:
    connection = op.get_bind()
    processed_ids: set[str] = set()
    for update in _FAQ_UPDATES:
        possible_questions = (update["question"], *update["legacy_questions"])
        for old_question in possible_questions:
            rows = connection.execute(
                sa.text(
                    """
                    SELECT faqs.*, ticket_categories.name AS category_name
                    FROM faqs
                    JOIN ticket_categories ON ticket_categories.id = faqs.category_id
                    WHERE ticket_categories.name=:category
                      AND faqs.question=:question
                    """
                ),
                {"category": update["category"], "question": old_question},
            ).mappings()
            for faq in rows:
                if faq["id"] in processed_ids:
                    continue
                processed_ids.add(faq["id"])
                values = _faq_values(category_name=update["category"], faq=update)
                _update_faq(connection, faq=faq, values=values)

    for category_name, question in _RETIRED_FAQS:
        rows = connection.execute(
            sa.text(
                """
                SELECT faqs.*
                FROM faqs
                JOIN ticket_categories ON ticket_categories.id = faqs.category_id
                WHERE ticket_categories.name=:category
                  AND faqs.question=:question
                """
            ),
            {"category": category_name, "question": question},
        ).mappings()
        for faq in rows:
            _archive_faq(connection, faq=faq)


def downgrade() -> None:
    # Reverting would restore retired or outdated FAQ content.
    pass
