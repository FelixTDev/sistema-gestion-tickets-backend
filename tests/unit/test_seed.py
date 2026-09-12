from sqlmodel import Session, create_engine, select


def test_demo_seed_is_idempotent():
    from app.db.models import Role, User
    from app.seed.demo_data import seed_demo_data

    engine = create_engine("sqlite://")
    from sqlmodel import SQLModel

    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        seed_demo_data(session)
        seed_demo_data(session)
        users = session.exec(select(User)).all()
        roles = session.exec(select(Role)).all()

    assert len(users) == 3
    assert {user.email for user in users} == {
        "cliente@demo.com",
        "asesor@demo.com",
        "supervisor@demo.com",
    }
    assert len(roles) == 3
