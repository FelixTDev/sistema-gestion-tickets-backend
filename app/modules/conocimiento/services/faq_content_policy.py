"""Rules for FAQ answers that are safe to publish without a source."""

SAFE_UNVERIFIED_FINANCIAL_INFORMATION = (
    "No tengo información verificada para responder esta consulta financiera."
)

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

_SAFE_UNVERIFIED_ANSWERS = frozenset(
    {
        SAFE_UNVERIFIED_FINANCIAL_INFORMATION,
        SAFE_UNVERIFIED_CLAIM_STATUS,
        SAFE_UNVERIFIED_LOAN_APPROVAL,
        SAFE_UNVERIFIED_CARD_ACTIVITY,
    }
)


def is_safe_unverified_faq(faq: object) -> bool:
    """Allow only the reviewed, source-less neutral FAQ answers in chat."""
    return (
        getattr(faq, "source_url", None) is None
        and getattr(faq, "source_verified_at", None) is None
        and getattr(faq, "answer", None) in _SAFE_UNVERIFIED_ANSWERS
    )
