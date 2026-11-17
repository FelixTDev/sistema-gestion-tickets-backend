from types import SimpleNamespace

from sqlmodel import Session, SQLModel, create_engine, select

from app.modules.chatbot.services.chatbot_service import ChatbotService
from app.modules.chatbot.services.faq_aliases import FAQ_SEARCH_ALIASES
from app.modules.conocimiento.models.category import TicketCategory
from app.modules.conocimiento.models.faq import FAQ
from app.modules.conocimiento.services.faq_content_policy import (
    APPROVED_SOURCELESS_ANSWERS,
    SAFE_UNVERIFIED_FINANCIAL_INFORMATION,
    SBS_CLAIMS_REGULATION_URL,
)
from app.seed.demo_data import CATEGORIES, DEMO_FAQS, seed_demo_data


def test_seeded_faqs_and_private_aliases_resolve_to_reviewed_answers() -> None:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    service = ChatbotService()
    with Session(engine) as session:
        seed_demo_data(session, include_operational_data=False)
        faqs = session.exec(select(FAQ)).all()
        active_questions = {
            faq.question for faq in service._available_public_faqs(session)
        }
        faq_by_question = {faq.question: faq for faq in faqs if faq.is_active}
        answers = {entry[1]: entry[2] for entry in DEMO_FAQS}

        assert active_questions == set(answers)
        assert set(FAQ_SEARCH_ALIASES) == set(answers)
        assert {row.name for row in session.exec(select(TicketCategory)).all()} == set(
            CATEGORIES
        )
        assert "¿Cómo me afilio a Banca Móvil?" not in active_questions
        assert "¿Cómo ingreso a la banca digital?" not in active_questions
        assert (
            "¿Qué hago si olvidé mi contraseña de Banca Móvil?" not in active_questions
        )
        assert "¿Cuál es el horario de Banca por Teléfono?" not in active_questions

        for question, answer in answers.items():
            assert answer in APPROVED_SOURCELESS_ANSWERS
            assert answer != SAFE_UNVERIFIED_FINANCIAL_INFORMATION
            for query in (question, *FAQ_SEARCH_ALIASES[question]):
                matches = service._find_matches(session, query)
                assert matches, query
                assert matches[0].faq.question == question, query
                response = service._response_for_matches(
                    SimpleNamespace(context_json={}), matches
                )
                assert response["resolved"] is True, query
                assert response["answer"] == answer, query
                assert response["fallback_reason"] is None, query
                assert response["answer"] != SAFE_UNVERIFIED_FINANCIAL_INFORMATION

        claim = faq_by_question[
            "¿Cuál es la diferencia entre un reclamo y un requerimiento?"
        ]
        assert claim.source_url == SBS_CLAIMS_REGULATION_URL
        assert (
            service._public_sources(
                service._find_matches(
                    session,
                    "¿Cuál es la diferencia entre un reclamo y un requerimiento?",
                )[:1]
            )[0]["url"]
            == SBS_CLAIMS_REGULATION_URL
        )
    engine.dispose()
