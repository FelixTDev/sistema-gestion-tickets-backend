from collections.abc import Generator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.db import models  # noqa: F401
from app.db.session import get_session
from app.main import app
from app.modules.conocimiento.models.category import TicketCategory
from app.modules.tickets.models.ticket import Ticket, TicketStatus
from app.seed.demo_data import seed_demo_data


@pytest.fixture
def pagination_client() -> Generator[tuple[TestClient, object], None, None]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        seed_demo_data(session, include_operational_data=False)

    def override_session() -> Generator[Session, None, None]:
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    with TestClient(app) as client:
        yield client, engine
    app.dependency_overrides.clear()


def login(client: TestClient, email: str, password: str = "demo-password-local") -> str:
    response = client.post(
        "/api/v1/auth/login", json={"email": email, "password": password}
    )
    assert response.status_code == 200
    return response.json()["access_token"]


def category_id(engine: object) -> str:
    with Session(engine) as session:
        category = session.exec(select(TicketCategory)).first()
        assert category is not None
        return category.id


def create_ticket(
    client: TestClient,
    token: str,
    category: str,
    *,
    subject: str = "Consulta de prueba",
    description: str = "Descripción de la consulta de prueba.",
    priority: str = "MEDIA",
) -> dict:
    response = client.post(
        "/api/v1/tickets",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "category_id": category,
            "subject": subject,
            "description": description,
            "priority": priority,
        },
    )
    assert response.status_code == 201
    return response.json()


def set_ticket_fields(engine: object, ticket_id: str, **values: object) -> None:
    with Session(engine) as session:
        ticket = session.get(Ticket, ticket_id)
        assert ticket is not None
        for field, value in values.items():
            setattr(ticket, field, value)
        session.add(ticket)
        session.commit()


def paginated(response) -> dict:
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"page", "page_size", "total", "total_pages", "items"}
    return body


def test_returns_first_intermediate_and_out_of_range_pages(pagination_client):
    client, engine = pagination_client
    token = login(client, "cliente@demo.com")
    category = category_id(engine)
    tickets = [
        create_ticket(client, token, category, subject=f"Ticket {index}")
        for index in range(5)
    ]

    first = paginated(
        client.get(
            "/api/v1/tickets/mine",
            params={"page": 1, "page_size": 2},
            headers={"Authorization": f"Bearer {token}"},
        )
    )
    middle = paginated(
        client.get(
            "/api/v1/tickets/mine",
            params={"page": 2, "page_size": 2},
            headers={"Authorization": f"Bearer {token}"},
        )
    )
    outside = paginated(
        client.get(
            "/api/v1/tickets/mine",
            params={"page": 4, "page_size": 2},
            headers={"Authorization": f"Bearer {token}"},
        )
    )

    assert first["page"] == 1
    assert first["page_size"] == 2
    assert first["total"] == 5
    assert first["total_pages"] == 3
    assert len(first["items"]) == 2
    assert len(middle["items"]) == 2
    assert outside["items"] == []
    assert {item["id"] for item in first["items"]}.isdisjoint(
        {item["id"] for item in middle["items"]}
    )
    assert {item["id"] for item in first["items"] + middle["items"]}.issubset(
        {ticket["id"] for ticket in tickets}
    )


@pytest.mark.parametrize(
    "params",
    [{"page": 0}, {"page": -1}, {"page_size": 0}, {"page_size": 101}],
)
def test_rejects_invalid_pagination_params(pagination_client, params):
    client, _ = pagination_client
    token = login(client, "cliente@demo.com")

    response = client.get(
        "/api/v1/tickets/mine",
        params=params,
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 422


def test_searches_code_subject_description_and_authorized_client(pagination_client):
    client, engine = pagination_client
    client_token = login(client, "cliente@demo.com")
    supervisor_token = login(client, "supervisor@demo.com")
    category = category_id(engine)
    ticket = create_ticket(
        client,
        client_token,
        category,
        subject="Asunto distinguible",
        description="Descripción distinguible del caso",
    )

    for term in (ticket["tracking_code"], "distinguible", "caso"):
        body = paginated(
            client.get(
                "/api/v1/tickets",
                params={"search": term, "page": 1, "page_size": 20},
                headers={"Authorization": f"Bearer {supervisor_token}"},
            )
        )
        assert [item["id"] for item in body["items"]] == [ticket["id"]]

    registered = client.post(
        "/api/v1/auth/register",
        json={
            "full_name": "Cliente Buscable",
            "email": "cliente.buscable@example.com",
            "password": "Demo-password-123",
        },
    )
    assert registered.status_code == 201
    other_token = login(client, "cliente.buscable@example.com", "Demo-password-123")
    other_ticket = create_ticket(
        client,
        other_token,
        category,
        subject="Caso de otra persona",
    )

    body = paginated(
        client.get(
            "/api/v1/tickets",
            params={"search": "Cliente Buscable", "page": 1, "page_size": 20},
            headers={"Authorization": f"Bearer {supervisor_token}"},
        )
    )
    assert [item["id"] for item in body["items"]] == [other_ticket["id"]]


def test_combined_filters_and_inclusive_date_boundaries(pagination_client):
    client, engine = pagination_client
    client_token = login(client, "cliente@demo.com")
    supervisor_token = login(client, "supervisor@demo.com")
    category = category_id(engine)
    target = create_ticket(
        client,
        client_token,
        category,
        priority="URGENTE",
        subject="Filtro combinado",
    )
    other = create_ticket(client, client_token, category, priority="BAJA")
    boundary = datetime(2026, 1, 1, 12, tzinfo=UTC)
    set_ticket_fields(
        engine,
        target["id"],
        status=TicketStatus.ASIGNADO,
        source="MANUAL",
        created_at=boundary,
        assigned_advisor_id=None,
    )
    set_ticket_fields(
        engine,
        other["id"],
        created_at=boundary - timedelta(seconds=1),
    )

    response = client.get(
        "/api/v1/tickets",
        params={
            "status": "ASIGNADO",
            "priority": "URGENTE",
            "category_id": category,
            "source": "MANUAL",
            "client_id": target["client_id"],
            "created_from": boundary.isoformat().replace("+00:00", "Z"),
            "created_to": boundary.isoformat().replace("+00:00", "Z"),
            "page": 1,
            "page_size": 20,
        },
        headers={"Authorization": f"Bearer {supervisor_token}"},
    )
    body = paginated(response)

    assert body["total"] == 1
    assert body["items"][0]["id"] == target["id"]


def test_stable_order_and_empty_search_result(pagination_client):
    client, engine = pagination_client
    token = login(client, "cliente@demo.com")
    category = category_id(engine)
    first = create_ticket(client, token, category, subject="Mismo instante 1")
    second = create_ticket(client, token, category, subject="Mismo instante 2")
    timestamp = datetime(2026, 2, 1, 10, tzinfo=UTC)
    set_ticket_fields(engine, first["id"], created_at=timestamp)
    set_ticket_fields(engine, second["id"], created_at=timestamp)

    headers = {"Authorization": f"Bearer {token}"}
    first_read = paginated(
        client.get(
            "/api/v1/tickets/mine",
            params={"page": 1, "page_size": 20},
            headers=headers,
        )
    )
    second_read = paginated(
        client.get(
            "/api/v1/tickets/mine",
            params={"page": 1, "page_size": 20},
            headers=headers,
        )
    )
    empty = paginated(
        client.get(
            "/api/v1/tickets/mine",
            params={"search": "no existe", "page": 1, "page_size": 20},
            headers=headers,
        )
    )

    assert [item["id"] for item in first_read["items"]] == [
        item["id"] for item in second_read["items"]
    ]
    assert empty == {
        "page": 1,
        "page_size": 20,
        "total": 0,
        "total_pages": 0,
        "items": [],
    }


def test_roles_and_legacy_list_compatibility(pagination_client):
    client, engine = pagination_client
    client_token = login(client, "cliente@demo.com")
    advisor_token = login(client, "asesor@demo.com")
    supervisor_token = login(client, "supervisor@demo.com")
    category = category_id(engine)
    own = create_ticket(client, client_token, category)

    legacy_mine = client.get(
        "/api/v1/tickets/mine",
        headers={"Authorization": f"Bearer {client_token}"},
    )
    advisor = client.get(
        "/api/v1/tickets",
        params={"page": 1, "page_size": 20},
        headers={"Authorization": f"Bearer {advisor_token}"},
    )
    supervisor = client.get(
        "/api/v1/tickets",
        params={"page": 1, "page_size": 20},
        headers={"Authorization": f"Bearer {supervisor_token}"},
    )
    forbidden = client.get(
        "/api/v1/tickets",
        params={"page": 1, "page_size": 20},
        headers={"Authorization": f"Bearer {client_token}"},
    )
    unauthenticated = client.get(
        "/api/v1/tickets/mine", params={"page": 1, "page_size": 20}
    )

    assert legacy_mine.status_code == 200
    assert isinstance(legacy_mine.json(), list)
    assert any(item["id"] == own["id"] for item in legacy_mine.json())
    assert own["id"] not in {item["id"] for item in paginated(advisor)["items"]}
    assert own["id"] in {item["id"] for item in paginated(supervisor)["items"]}
    assert forbidden.status_code == 403
    assert unauthenticated.status_code == 401
    assert "password_hash" not in advisor.text


def test_advisor_filter_returns_only_the_requested_assignment(pagination_client):
    client, engine = pagination_client
    client_token = login(client, "cliente@demo.com")
    supervisor_token = login(client, "supervisor@demo.com")
    advisor_login = client.post(
        "/api/v1/auth/login",
        json={"email": "asesor@demo.com", "password": "demo-password-local"},
    )
    assert advisor_login.status_code == 200
    advisor_token = advisor_login.json()["access_token"]
    advisor_id = advisor_login.json()["user"]["id"]
    category = category_id(engine)
    assigned = create_ticket(client, client_token, category, subject="Asignado")
    unassigned = create_ticket(client, client_token, category, subject="Sin asignar")
    set_ticket_fields(engine, assigned["id"], assigned_advisor_id=advisor_id)

    response = client.get(
        "/api/v1/tickets",
        params={"assigned_advisor_id": advisor_id, "page": 1, "page_size": 20},
        headers={"Authorization": f"Bearer {advisor_token}"},
    )
    supervisor_response = client.get(
        "/api/v1/tickets",
        params={"assigned_advisor_id": advisor_id, "page": 1, "page_size": 20},
        headers={"Authorization": f"Bearer {supervisor_token}"},
    )

    assert [item["id"] for item in paginated(response)["items"]] == [assigned["id"]]
    assert [item["id"] for item in paginated(supervisor_response)["items"]] == [
        assigned["id"]
    ]
    assert unassigned["id"] not in {item["id"] for item in paginated(response)["items"]}


def test_client_cannot_escape_own_scope_with_client_filter(pagination_client):
    client, engine = pagination_client
    own_token = login(client, "cliente@demo.com")
    registered = client.post(
        "/api/v1/auth/register",
        json={
            "full_name": "Segundo cliente",
            "email": "segundo.cliente@example.com",
            "password": "Demo-password-123",
        },
    )
    assert registered.status_code == 201
    other_token = login(client, "segundo.cliente@example.com", "Demo-password-123")
    category = category_id(engine)
    own = create_ticket(client, own_token, category, subject="Propio")
    other = create_ticket(client, other_token, category, subject="Ajeno")

    response = client.get(
        "/api/v1/tickets/mine",
        params={"client_id": other["client_id"], "page": 1, "page_size": 20},
        headers={"Authorization": f"Bearer {own_token}"},
    )
    body = paginated(response)

    assert [item["id"] for item in body["items"]] == [own["id"]]

    search_response = client.get(
        "/api/v1/tickets/mine",
        params={"search": "Segundo cliente", "page": 1, "page_size": 20},
        headers={"Authorization": f"Bearer {own_token}"},
    )
    assert paginated(search_response)["items"] == []
