"""Safely publish the curated chatbot FAQ catalog without reseeding operations."""

from sqlmodel import Session

from app.db.session import engine
from app.seed.demo_data import seed_chatbot_knowledge


def main() -> None:
    with Session(engine) as session:
        count = seed_chatbot_knowledge(session)
    print(f"Base de conocimiento actualizada: {count} FAQs; tickets intactos.")


if __name__ == "__main__":
    main()
