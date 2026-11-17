"""Rules for the small set of reviewed FAQ answers used without a source."""

from datetime import UTC, datetime

SAFE_UNVERIFIED_FINANCIAL_INFORMATION = (
    "No tengo información verificada para responder esta consulta financiera."
)

# Kept as named legacy values for compatibility with migration and test imports.
# These generic responses are intentionally not in APPROVED_SOURCELESS_ANSWERS.
SAFE_UNVERIFIED_CLAIM_STATUS = (
    "Para consultar el estado de un reclamo formal, sigue el canal indicado "
    "por la entidad y ten a mano tu código de seguimiento. El ticket de este "
    "portal no equivale a un reclamo formal ante esa entidad."
)
SAFE_UNVERIFIED_LOAN_APPROVAL = (
    "No. La aprobación y las condiciones dependen de la evaluación que realiza "
    "la entidad que ofrece el préstamo. No compartas datos financieros "
    "sensibles en este chat."
)
SAFE_UNVERIFIED_CARD_ACTIVITY = (
    "Si no reconoces una operación, contacta cuanto antes a tu entidad "
    "financiera por un canal que sepas que es oficial. No compartas aquí el "
    "número completo de tu tarjeta, claves ni códigos."
)

FAQ_CARD_REQUIREMENTS_ANSWER = (
    "Los requisitos dependen del emisor y del producto. Antes de solicitar una "
    "tarjeta, revisa las condiciones, los costos y los criterios de evaluación "
    "publicados por quien la ofrece. Este prototipo no emite tarjetas ni evalúa "
    "solicitudes."
)
FAQ_PORTAL_ACCESS_ANSWER = (
    "Para ingresar a este portal, selecciona Ingresar y usa la cuenta registrada. "
    "Si te refieres a la banca digital o aplicación de otra entidad, este proyecto "
    "no administra ese acceso; consulta al proveedor por un canal oficial. No "
    "compartas contraseñas ni códigos en el chat."
)
FAQ_ACCOUNT_BALANCE_ANSWER = (
    "Este prototipo no está conectado a cuentas y no puede consultar saldos ni "
    "movimientos. Para una cuenta real, utiliza los canales oficiales de su "
    "proveedor. No compartas aquí credenciales ni datos financieros."
)
FAQ_BLOCKED_CARD_ANSWER = (
    "Este prototipo no puede bloquear ni desbloquear tarjetas. Si se trata de una "
    "tarjeta real, comunícate con su emisor por un canal oficial para que te "
    "indique cómo proceder. No compartas aquí el número completo, claves ni "
    "códigos."
)
FAQ_CREDIT_INFORMATION_ANSWER = (
    "Este prototipo no ofrece ni cotiza créditos. Para revisar una oferta real, "
    "consulta las condiciones y costos publicados por quien ofrece el producto. "
    "No compartas aquí documentos ni información financiera sensible."
)
FAQ_CLAIM_STATUS_ANSWER = (
    "Este portal solo muestra las solicitudes creadas aquí; no consulta reclamos "
    "formales registrados ante otras entidades. Para revisar una solicitud del "
    "portal, entra en Mis solicitudes. Para un reclamo formal, consulta el canal "
    "donde lo registraste y utiliza su código de seguimiento."
)
FAQ_ACCOUNT_STATEMENT_ANSWER = (
    "Este prototipo no accede a cuentas ni emite estados de cuenta. Solicítalo al "
    "proveedor de tu cuenta mediante sus canales oficiales. No envíes aquí "
    "documentos ni datos financieros reales."
)
FAQ_ADVISOR_CONTACT_ANSWER = (
    "No hay atención humana en vivo desde este chat. Si quieres que revisen tu "
    "caso, puedes preparar una solicitud, revisar sus datos y confirmar su "
    "creación. Luego podrás seguirla en Mis solicitudes."
)
FAQ_ONLINE_CARD_PURCHASES_ANSWER = (
    "Este prototipo no cambia la configuración de tarjetas. Para una tarjeta real, "
    "consulta con su emisor por un canal oficial qué controles están disponibles. "
    "No compartas aquí datos de la tarjeta ni códigos de seguridad."
)
FAQ_PORTAL_PASSWORD_ANSWER = (
    "Este asistente no puede ver ni restablecer tu contraseña. Para el portal, usa "
    "las opciones de recuperación disponibles en la pantalla de acceso, si están "
    "habilitadas. Si te refieres a la aplicación de otra entidad, este prototipo "
    "no administra ese acceso. No escribas contraseñas ni códigos en el chat."
)
FAQ_SAVINGS_ACCOUNT_ANSWER = (
    "Los requisitos y condiciones dependen del proveedor y del tipo de cuenta. "
    "Este prototipo no abre cuentas; consulta las condiciones publicadas por quien "
    "ofrece el producto."
)
FAQ_ACCOUNT_TYPES_ANSWER = (
    "Existen distintos tipos de cuentas y depósitos. Su disponibilidad, costos y "
    "condiciones dependen del producto y del proveedor. Este prototipo no ofrece "
    "cuentas."
)
FAQ_LOAN_APPROVAL_ANSWER = (
    "No. Este asistente no evalúa solicitudes ni puede anticipar una aprobación. "
    "La evaluación corresponde a quien ofrece el préstamo. No compartas aquí "
    "datos financieros sensibles."
)
FAQ_CLAIM_VS_REQUEST_ANSWER = (
    "En términos generales, un reclamo expresa insatisfacción con una operación, "
    "producto o servicio recibido; un requerimiento solicita información o una "
    "acción. Este portal académico registra tickets y no tramita reclamos "
    "formales."
)
FAQ_FORMAL_CLAIM_ANSWER = (
    "Este portal no presenta reclamos formales: solo permite registrar solicitudes "
    "de demostración. Para un reclamo real, sigue el procedimiento oficial del "
    "proveedor correspondiente. No envíes documentos ni datos sensibles por este "
    "chat."
)
FAQ_FORMAL_CLAIM_TIMING_ANSWER = (
    "Este prototipo no tramita reclamos formales ni puede consultar sus plazos. El "
    "plazo aplicable depende del procedimiento correspondiente; confírmalo con el "
    "canal oficial donde registraste el reclamo. Las solicitudes de este portal se "
    "siguen desde Mis solicitudes."
)
FAQ_PHONE_SUPPORT_ANSWER = (
    "Este prototipo no brinda atención telefónica ni transferencia en vivo. No "
    "confirma horarios de otros proveedores. Puedes usar el asistente o registrar "
    "una solicitud."
)
FAQ_UNRECOGNIZED_CARD_ACTIVITY_ANSWER = (
    "Si ves una operación que no reconoces, comunícate cuanto antes con el emisor "
    "de tu tarjeta por un canal oficial y sigue sus indicaciones. Este prototipo no "
    "está conectado a movimientos ni puede gestionar la tarjeta. No compartas aquí "
    "números completos, claves ni códigos."
)

APPROVED_SOURCELESS_ANSWERS = frozenset(
    {
        FAQ_CARD_REQUIREMENTS_ANSWER,
        FAQ_PORTAL_ACCESS_ANSWER,
        FAQ_ACCOUNT_BALANCE_ANSWER,
        FAQ_BLOCKED_CARD_ANSWER,
        FAQ_CREDIT_INFORMATION_ANSWER,
        FAQ_CLAIM_STATUS_ANSWER,
        FAQ_ACCOUNT_STATEMENT_ANSWER,
        FAQ_ADVISOR_CONTACT_ANSWER,
        FAQ_ONLINE_CARD_PURCHASES_ANSWER,
        FAQ_PORTAL_PASSWORD_ANSWER,
        FAQ_SAVINGS_ACCOUNT_ANSWER,
        FAQ_ACCOUNT_TYPES_ANSWER,
        FAQ_LOAN_APPROVAL_ANSWER,
        FAQ_CLAIM_VS_REQUEST_ANSWER,
        FAQ_FORMAL_CLAIM_ANSWER,
        FAQ_FORMAL_CLAIM_TIMING_ANSWER,
        FAQ_PHONE_SUPPORT_ANSWER,
        FAQ_UNRECOGNIZED_CARD_ACTIVITY_ANSWER,
    }
)

SBS_CLAIMS_REGULATION_URL = (
    "https://intranet2.sbs.gob.pe/dv_int_cn/2230/v6.0/Adjuntos/4036-2022.R%20.pdf"
)
SBS_CLAIMS_REGULATION_VERIFIED_AT = datetime(2026, 11, 17, tzinfo=UTC)


def is_safe_unverified_faq(faq: object) -> bool:
    """Allow only exact, reviewed source-less FAQ answers in chat."""
    return (
        getattr(faq, "source_url", None) is None
        and getattr(faq, "source_verified_at", None) is None
        and getattr(faq, "answer", None) in APPROVED_SOURCELESS_ANSWERS
    )
