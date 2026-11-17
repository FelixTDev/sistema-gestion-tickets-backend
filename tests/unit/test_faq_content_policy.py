from types import SimpleNamespace

import pytest

from app.modules.conocimiento.services.faq_content_policy import (
    SAFE_UNVERIFIED_FINANCIAL_INFORMATION,
    is_safe_unverified_faq,
)


@pytest.mark.parametrize(
    "answer",
    [
        SAFE_UNVERIFIED_FINANCIAL_INFORMATION,
        "Consulta la información vigente de tu entidad financiera.",
    ],
)
def test_only_curated_source_less_general_answers_are_available(answer: str) -> None:
    faq = SimpleNamespace(
        answer=answer,
        source_url=None,
        source_verified_at=None,
    )

    assert is_safe_unverified_faq(faq) is (
        answer == SAFE_UNVERIFIED_FINANCIAL_INFORMATION
    )


def test_safe_answer_is_not_available_when_it_carries_unverified_source_metadata() -> (
    None
):
    faq = SimpleNamespace(
        answer=SAFE_UNVERIFIED_FINANCIAL_INFORMATION,
        source_url="https://example.invalid/",
        source_verified_at=None,
    )

    assert is_safe_unverified_faq(faq) is False
