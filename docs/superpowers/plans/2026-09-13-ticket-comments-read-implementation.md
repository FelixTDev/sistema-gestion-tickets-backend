# Ticket Comments Read Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add authenticated retrieval of ticket comments at `GET /api/v1/tickets/{ticket_id}/comments` while reusing the existing ticket authorization and public `CommentRead` contract.

**Architecture:** Keep the existing `router/controller → service → repository → model` flow. The router will depend on the current authenticated-user dependency, `TicketService` will reuse `_get_authorized` so clients, advisors, and supervisors follow the exact same access rule as `GET /api/v1/tickets/{ticket_id}`, and `TicketRepository` will provide the ordered comment query.

**Tech Stack:** FastAPI, SQLModel, Pydantic response models, Pytest/TestClient, Ruff, Alembic, MySQL-compatible SQLAlchemy queries.

## Global Constraints

- Work exclusively in `C:\dev\sistema-gestion-tickets\backend`.
- Do not modify the frontend or files outside the backend repository.
- Do not change authentication, chatbot, ticket models, migrations, or existing endpoint behavior.
- Require authentication and do not expose passwords, password hashes, tokens, or extra sensitive fields.
- Return `list[CommentRead]`, HTTP 200 with `[]` for tickets without comments, and comments ordered ascending by `created_at`.
- Preserve the existing authorization rule: clients may access only owned tickets; advisors and supervisors use the current internal access rule.
- Do not add a migration because `ticket_comments` already exists and `TicketComment` already contains all fields needed by `CommentRead`.
- Create no intermediate commits; create exactly `fix(tickets): add comment retrieval endpoint` after final validation.

---

### Task 1: Add failing integration coverage

**Files:**
- Create: `tests/integration/test_ticket_comments_read.py`
- Read-only references: `tests/integration/test_tickets.py`, `app/modules/tickets/api/router.py`, `app/modules/tickets/services/ticket_service.py`, `app/modules/tickets/repositories/ticket_repository.py`

**Interfaces:**
- Consumes the existing `ticket_client` fixture behavior, demo accounts, `POST /api/v1/tickets`, `POST /api/v1/tickets/{ticket_id}/comments`, and `GET /api/v1/tickets/{ticket_id}` authorization behavior.
- Produces failing tests for `GET /api/v1/tickets/{ticket_id}/comments`; no production code is changed in this task.

- [x] **Step 1: Write the failing tests**

Create a SQLite `StaticPool` fixture using `SQLModel.metadata.create_all`, `seed_demo_data`, and the existing `get_session` override. Add tests that create a manual ticket for `cliente@demo.com`, add comments through the existing endpoint, and assert:

```python
def test_owner_gets_comments_in_ascending_created_at_order(ticket_client):
    client, engine = ticket_client
    client_token = login(client, "cliente@demo.com")
    ticket = create_ticket(client, client_token, category_id(engine))
    for content in ("Primer comentario", "Segundo comentario"):
        response = client.post(
            f"/api/v1/tickets/{ticket['id']}/comments",
            headers={"Authorization": f"Bearer {client_token}"},
            json={"content": content},
        )
        assert response.status_code == 201

    response = client.get(
        f"/api/v1/tickets/{ticket['id']}/comments",
        headers={"Authorization": f"Bearer {client_token}"},
    )

    assert response.status_code == 200
    assert [item["content"] for item in response.json()] == [
        "Primer comentario",
        "Segundo comentario",
    ]
    assert list(response.json()[0]) == [
        "id",
        "ticket_id",
        "author_id",
        "content",
        "created_at",
    ]
    assert "password_hash" not in response.text
    assert "access_token" not in response.text
```

Also cover an owner with no comments returning `[]`, a second client returning 403, an advisor and supervisor returning 200, no token returning 401, and an unknown ticket returning 404. Use direct database timestamp setup for two comments if necessary to prove ascending ordering independent of insertion order.

- [x] **Step 2: Run the focused tests and verify RED**

Run:

```bash
.\.venv\Scripts\python.exe -m pytest tests/integration/test_ticket_comments_read.py -q
```

Expected result: the new tests fail because `GET /api/v1/tickets/{ticket_id}/comments` is not registered yet, while setup calls to existing ticket endpoints continue to work.

### Task 2: Implement repository, service, and router layers

**Files:**
- Modify: `app/modules/tickets/repositories/ticket_repository.py`
- Modify: `app/modules/tickets/services/ticket_service.py`
- Modify: `app/modules/tickets/api/router.py`
- Reuse: `app/modules/tickets/schemas/ticket.py` (`CommentRead`)

**Interfaces:**
- `TicketRepository.list_comments(session: Session, ticket_id: str) -> list[TicketComment]` executes one SQLModel query ordered by `TicketComment.created_at.asc()`.
- `TicketService.comments(session: Session, ticket_id: str, actor: AuthenticatedUser) -> list[TicketComment]` first calls the existing `_get_authorized(session, ticket_id, actor)`, then returns `repository.list_comments(...)`.
- The router adds `GET /{ticket_id}/comments` with `CurrentUser`, `SessionDependency`, an explicit `list[CommentRead]` return type, and `response_model=list[CommentRead]`.

- [x] **Step 1: Add the minimal repository query**

Add this method without changing existing repository methods:

```python
def list_comments(self, session: Session, ticket_id: str) -> list[TicketComment]:
    return list(
        session.exec(
            select(TicketComment)
            .where(TicketComment.ticket_id == ticket_id)
            .order_by(TicketComment.created_at.asc())
        ).all()
    )
```

- [x] **Step 2: Add the service method using existing authorization**

Add this method to `TicketService`:

```python
def comments(
    self, session: Session, ticket_id: str, actor: AuthenticatedUser
) -> list[TicketComment]:
    self._get_authorized(session, ticket_id, actor)
    return self.repository.list_comments(session, ticket_id)
```

This preserves 404 for a missing ticket and 403 for a non-owner client through the already-tested `_get_authorized` path. Advisors and supervisors retain the same access granted by the current `GET /tickets/{ticket_id}` implementation.

- [x] **Step 3: Add the route with the existing public schema**

Add the following operation before the dynamic ticket path is irrelevant to routing order because its static suffix is matched by FastAPI’s route tree:

```python
@router.get("/{ticket_id}/comments", response_model=list[CommentRead])
def get_comments(
    ticket_id: str,
    session: SessionDependency,
    current_user: CurrentUser,
    service: TicketServiceDependency,
) -> list[CommentRead]:
    return service.comments(session, ticket_id, current_user)
```

Import `CommentRead` from `app.modules.tickets.schemas.ticket`. Do not add fields or a parallel response model.

### Task 3: Verify, document, and finish

**Files:**
- Modify: `README.md` only if the endpoint is not already documented.
- Modify: `docs/superpowers/plans/2026-09-13-ticket-comments-read-implementation.md` only to check completed steps if desired.

**Interfaces:**
- Produces the final authenticated endpoint and regression-safe test suite.

- [x] **Step 1: Run focused tests after implementation**

Run:

```bash
.\.venv\Scripts\python.exe -m pytest tests/integration/test_ticket_comments_read.py -q
```

Expected result: all comment retrieval tests pass, including empty results, authorization, ordering, exact public fields, and absence of sensitive fields.

- [x] **Step 2: Run the full validation suite**

Run from `C:\dev\sistema-gestion-tickets\backend`:

```bash
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\ruff.exe check .
.\.venv\Scripts\ruff.exe format --check .
.\.venv\Scripts\python.exe -m alembic check
git diff --check
```

Expected result: all tests pass, Ruff reports no errors, formatting is clean, Alembic reports no new upgrade operations, and `git diff --check` is clean. No migration is created or required.

- [x] **Step 3: Perform security and scope review**

Confirm the response is produced by `CommentRead` only, the route requires `CurrentUser`, authorization calls `_get_authorized`, the query returns only comments for the requested ticket, and no frontend, external file, model, migration, authentication, chatbot, or unrelated ticket behavior changed. If README lacks the endpoint, add one concise example using `Authorization: Bearer <access_token>` without real secrets.

- [x] **Step 4: Check repository state and create the single commit**

Run:

```bash
git status --short
git diff --name-only
git add docs/superpowers/plans/2026-09-13-ticket-comments-read-implementation.md app/modules/tickets/repositories/ticket_repository.py app/modules/tickets/services/ticket_service.py app/modules/tickets/api/router.py tests/integration/test_ticket_comments_read.py README.md
git commit -m "fix(tickets): add comment retrieval endpoint"
git status --short
git log -1 --oneline
```

Expected result: the commit is created once with the exact requested message and the working tree is clean except for ignored local files such as `.env`, if present.

## Plan Self-Review

- Spec coverage: route contract, authentication, existing ticket authorization, empty list behavior, ascending order, exact `CommentRead` fields, error statuses, tests, validation commands, security review, no-migration confirmation, README check, and single final commit are covered above.
- Placeholder scan: every step contains concrete paths, interfaces, commands, or code.
- Type consistency: repository returns `list[TicketComment]`, service returns the same ORM list after authorization, and FastAPI serializes it through the existing `CommentRead` schema as `list[CommentRead]`.
- Scope check: only one read endpoint and its minimum repository/service/router/test/plan/documentation changes are allowed; no unrelated refactor is planned.
