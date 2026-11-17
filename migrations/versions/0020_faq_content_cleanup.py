"""Remove unsupported institution-specific claims from seeded FAQ content.

The migration updates only FAQs whose question matches the seeded catalog that
shipped before this revision, including its legacy card-requirements question.
Matching IDs are resolved from the target database at upgrade time.
"""

import json
import re
import unicodedata

import sqlalchemy as sa
from alembic import op

revision = "0020_faq_content_cleanup"
down_revision = "0019_chatbot_quality_sources"
branch_labels = None
depends_on = None

_UNVERIFIED_FINANCIAL_INFORMATION = (
    "No tengo información verificada para responder esta consulta financiera."
)

# Keys are the exact questions from the pre-cleanup seed. The one legacy alias
# is included because older installations can contain that question instead of
# the canonical card-requirements FAQ.
_FAQ_CONTENT = {
    "¿Qué requisitos necesito para solicitar una tarjeta?": (
        "¿Qué requisitos necesito para solicitar una tarjeta?",
        _UNVERIFIED_FINANCIAL_INFORMATION,
        "tarjeta,requisitos,solicitud,crédito,debito",
    ),
    "¿Qué requisitos necesito para una tarjeta?": (
        "¿Qué requisitos necesito para solicitar una tarjeta?",
        _UNVERIFIED_FINANCIAL_INFORMATION,
        "tarjeta,requisitos,solicitud,crédito,debito",
    ),
    "¿Cómo ingreso a la banca digital?": (
        "¿Cómo ingreso a la banca digital?",
        _UNVERIFIED_FINANCIAL_INFORMATION,
        "banca,aplicacion,acceso,ingreso,clave,contraseña,afiliación",
    ),
    "¿Cómo consulto el saldo de mi cuenta?": (
        "¿Cómo consulto el saldo de mi cuenta?",
        _UNVERIFIED_FINANCIAL_INFORMATION,
        "cuenta,saldo,movimientos,consulta,ahorros",
    ),
    "¿Qué hago si mi tarjeta está bloqueada?": (
        "¿Qué hago si mi tarjeta está bloqueada?",
        _UNVERIFIED_FINANCIAL_INFORMATION,
        "bloqueada,bloqueo,desbloqueo,perdida,robo,urgente",
    ),
    "¿Cómo solicito información sobre un crédito?": (
        "¿Cómo solicito información sobre un crédito?",
        _UNVERIFIED_FINANCIAL_INFORMATION,
        "credito,crédito,prestamo,préstamo,cuota,requisitos,tasa",
    ),
    "¿Cómo consulto el estado de un reclamo?": (
        "¿Cómo consulto el estado de un reclamo?",
        "Para consultar el estado de un reclamo formal, sigue el canal indicado "
        "por la entidad y ten a mano tu código de seguimiento. El ticket de este "
        "portal no equivale a un reclamo formal ante esa entidad.",
        "reclamo,estado,seguimiento,incidencia,queja,código",
    ),
    "¿Cómo solicito un estado de cuenta?": (
        "¿Cómo solicito un estado de cuenta?",
        _UNVERIFIED_FINANCIAL_INFORMATION,
        "estado,cuenta,documento,periodo,duplicado,extracto",
    ),
    "¿Cómo puedo contactar a un asesor?": (
        "¿Cómo puedo contactar a un asesor?",
        _UNVERIFIED_FINANCIAL_INFORMATION,
        "asesor,atencion,ayuda,telefono,contacto",
    ),
    "¿Puedo habilitar o deshabilitar compras por internet en mi tarjeta?": (
        "¿Puedo habilitar o deshabilitar compras por internet en mi tarjeta?",
        _UNVERIFIED_FINANCIAL_INFORMATION,
        "tarjeta,internet,compras,habilitar,deshabilitar,configurar,seguridad",
    ),
    "¿Cómo me afilio a Banca Móvil?": (
        "¿Cómo me afilio a Banca Móvil?",
        _UNVERIFIED_FINANCIAL_INFORMATION,
        "banca movil,activar,acceso,afiliacion,registro,aplicacion,app",
    ),
    "¿Qué hago si olvidé mi contraseña de Banca Móvil?": (
        "¿Qué hago si olvidé mi contraseña de Banca Móvil?",
        _UNVERIFIED_FINANCIAL_INFORMATION,
        "olvide,olvidé,contraseña,clave,recuperar,restablecer,banca movil",
    ),
    "¿Qué necesito para abrir una Cuenta de Ahorro?": (
        "¿Qué necesito para abrir una Cuenta de Ahorro?",
        _UNVERIFIED_FINANCIAL_INFORMATION,
        "cuenta ahorro abrir requisitos documento dni soles dolares",
    ),
    "¿Qué tipos de cuentas ofrece Banco GNB?": (
        "¿Qué tipos de cuentas ofrece una entidad financiera?",
        _UNVERIFIED_FINANCIAL_INFORMATION,
        "tipos cuenta corriente sueldo cts ahorro plazo opciones",
    ),
    "¿El asistente puede decirme si me aprobarán un préstamo?": (
        "¿El asistente puede decirme si me aprobarán un préstamo?",
        "No. La aprobación y las condiciones dependen de la evaluación que realiza "
        "la entidad que ofrece el préstamo. No compartas datos financieros "
        "sensibles en este chat.",
        "prestamo credito aprobacion evaluar oferta calificacion",
    ),
    "¿Cuál es la diferencia entre un reclamo y un requerimiento?": (
        "¿Cuál es la diferencia entre un reclamo y un requerimiento?",
        _UNVERIFIED_FINANCIAL_INFORMATION,
        "reclamo requerimiento diferencia consulta insatisfaccion solicitud",
    ),
    "¿Cómo presento un reclamo formal a Banco GNB?": (
        "¿Cómo presento un reclamo formal ante una entidad financiera?",
        _UNVERIFIED_FINANCIAL_INFORMATION,
        "presentar reclamo formal formulario queja canal",
    ),
    "¿Cuánto tarda la respuesta de un reclamo formal?": (
        "¿Cuánto tarda la respuesta de un reclamo formal?",
        _UNVERIFIED_FINANCIAL_INFORMATION,
        "plazo tiempo respuesta reclamo requerimiento dias habiles demora",
    ),
    "¿Cuál es el horario de Banca por Teléfono?": (
        "¿Cuál es el horario de Banca por Teléfono?",
        _UNVERIFIED_FINANCIAL_INFORMATION,
        "horario telefono atencion canal",
    ),
    "¿Qué hago si no reconozco una operación con mi tarjeta?": (
        "¿Qué hago si no reconozco una operación con mi tarjeta?",
        "Si no reconoces una operación, contacta cuanto antes a tu entidad "
        "financiera por un canal que sepas que es oficial. No compartas aquí el "
        "número completo de tu tarjeta, claves ni códigos.",
        "operacion no reconocida tarjeta fraude consumo desconocido reclamo",
    ),
}


def _normalize(value: str) -> str:
    without_accents = "".join(
        char
        for char in unicodedata.normalize("NFD", value)
        if unicodedata.category(char) != "Mn"
    )
    return re.sub(r"\s+", " ", without_accents).strip().casefold()


def _faq_values(
    *, faq_id: str, category_name: str, question: str, answer: str, keywords: str
) -> dict[str, object]:
    tags = json.dumps(
        [term.strip() for term in keywords.split(",") if term.strip()],
        ensure_ascii=False,
    )
    return {
        "id": faq_id,
        "title": question,
        "question": question,
        "answer": answer,
        "summary": answer[:1000],
        "keywords": keywords,
        "tags": tags,
        "synonyms": "[]",
        "normalized_content": _normalize(
            f"{question} {answer} {keywords} {category_name}"
        ),
        "intent": None,
        "source_url": None,
        "source_verified_at": None,
    }


def _update_faq_content(connection, content: dict[str, object]) -> None:
    connection.execute(
        sa.text(
            """
            UPDATE faqs
            SET title=:title,
                question=:question,
                answer=:answer,
                summary=:summary,
                keywords=:keywords,
                tags=:tags,
                synonyms=:synonyms,
                normalized_content=:normalized_content,
                normalized_title=:normalized_title,
                normalized_question=:normalized_question,
                intent=:intent,
                source_url=:source_url,
                source_verified_at=:source_verified_at
            WHERE id=:id
            """
        ),
        {
            **content,
            "normalized_title": _normalize(str(content["title"])),
            "normalized_question": _normalize(str(content["question"])),
        },
    )
    connection.execute(
        sa.text(
            """
            UPDATE faq_versions
            SET title=:title,
                question=:question,
                answer=:answer,
                summary=:summary,
                keywords=:keywords,
                tags=:tags,
                synonyms=:synonyms,
                normalized_content=:normalized_content,
                intent=:intent,
                source_url=:source_url,
                source_verified_at=:source_verified_at
            WHERE faq_id=:id
            """
        ),
        content,
    )


def upgrade() -> None:
    connection = op.get_bind()
    for previous_question, (question, answer, keywords) in _FAQ_CONTENT.items():
        rows = connection.execute(
            sa.text(
                """
                SELECT faqs.id, ticket_categories.name AS category_name
                FROM faqs
                JOIN ticket_categories ON ticket_categories.id = faqs.category_id
                WHERE faqs.question IN (:previous_question, :question)
                """
            ),
            {"previous_question": previous_question, "question": question},
        ).mappings()
        for faq in rows:
            content = _faq_values(
                faq_id=faq["id"],
                category_name=faq["category_name"],
                question=question,
                answer=answer,
                keywords=keywords,
            )
            _update_faq_content(connection, content)


def downgrade() -> None:
    # Do not reintroduce withdrawn, unsupported claims or their sources.
    pass
