from types import SimpleNamespace

import pytest

from app.modules.chatbot.services.guardrails import has_verified_official_source
from app.modules.conocimiento.schemas.faq import FAQCreate
from app.modules.conocimiento.services.faq_content_policy import (
    APPROVED_SOURCELESS_ANSWERS,
    SAFE_UNVERIFIED_FINANCIAL_INFORMATION,
    SBS_CLAIMS_REGULATION_URL,
    SBS_CLAIMS_REGULATION_VERIFIED_AT,
    is_safe_unverified_faq,
)


@pytest.mark.parametrize("answer", sorted(APPROVED_SOURCELESS_ANSWERS))
def test_only_exact_reviewed_answers_are_available_without_a_source(
    answer: str,
) -> None:
    faq = SimpleNamespace(
        answer=answer,
        source_url=None,
        source_verified_at=None,
    )

    assert is_safe_unverified_faq(faq) is True


@pytest.mark.parametrize(
    "answer",
    [
        SAFE_UNVERIFIED_FINANCIAL_INFORMATION,
        "Consulta la información vigente de tu entidad financiera.",
    ],
)
def test_unreviewed_or_repetitive_answers_are_not_available(answer: str) -> None:
    faq = SimpleNamespace(
        answer=answer,
        source_url=None,
        source_verified_at=None,
    )

    assert is_safe_unverified_faq(faq) is False


def test_reviewed_answer_is_not_treated_as_source_less_when_metadata_exists() -> None:
    faq = SimpleNamespace(
        answer=next(iter(APPROVED_SOURCELESS_ANSWERS)),
        source_url="https://example.invalid/",
        source_verified_at=None,
    )

    assert is_safe_unverified_faq(faq) is False


def test_sbs_citation_is_attached_to_a_verified_claim_source() -> None:
    faq = SimpleNamespace(
        answer=next(iter(APPROVED_SOURCELESS_ANSWERS)),
        source_url=SBS_CLAIMS_REGULATION_URL,
        source_verified_at=SBS_CLAIMS_REGULATION_VERIFIED_AT,
    )

    assert is_safe_unverified_faq(faq) is False
    assert (
        has_verified_official_source(
            SBS_CLAIMS_REGULATION_URL,
            SBS_CLAIMS_REGULATION_VERIFIED_AT,
        )
        is True
    )


def test_faq_source_contract_accepts_the_verified_official_sbs_host() -> None:
    faq = FAQCreate(
        category_id="category-id",
        title="Diferencia entre reclamo y requerimiento",
        question="¿Cuál es la diferencia entre un reclamo y un requerimiento?",
        answer=next(iter(APPROVED_SOURCELESS_ANSWERS)),
        keywords="reclamo,requerimiento",
        source_url=SBS_CLAIMS_REGULATION_URL,
        source_verified_at=SBS_CLAIMS_REGULATION_VERIFIED_AT,
    )

    assert faq.source_url == SBS_CLAIMS_REGULATION_URL
