# Advisor Directory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an authenticated supervisor-only directory endpoint that returns only active advisors with the existing safe public-user fields.

**Architecture:** Add a users router under the existing `/api/v1` router, a users service for the directory use case, and a repository query that joins users to roles and applies the active-advisor filter and stable ordering. Reuse the existing `require_roles("SUPERVISOR")` dependency and `PublicUser` schema so authorization and response filtering are not duplicated.

**Tech Stack:** FastAPI, SQLModel, Pydantic, MySQL/SQLite-compatible SQL, Pytest/TestClient, Ruff, Alembic.

## Global Constraints

- Work exclusively in `C:\dev\sistema-gestion-tickets\backend`.
- Do not modify the frontend or files outside the backend repository.
- Implement only `GET /api/v1/users/advisors` and its tests/documentation.
- Require a valid active authenticated user and allow only `SUPERVISOR`; clients, advisors, unauthenticated users, inactive users, and invalid tokens must not receive the directory.
- Return only `id`, `full_name`, `email`, and `role`; never return `password_hash`, tokens, secrets, or unrelated fields.
- Include only active users with role `ASESOR`; exclude `CLIENTE`, `SUPERVISOR`, and inactive advisors.
- Use one repository query with a role join and deterministic ordering by `full_name` and `id`; do not introduce N+1 role lookups.
- Preserve all existing authentication, chatbot, ticket, knowledge, report, model, and migration behavior.
- Do not create intermediate commits; create exactly `feat(users): add advisor directory endpoint` after final validation.

---

### Task 1: Add failing advisor-directory integration tests

**Files:**
- Create: `tests/integration/test_advisor_directory.py`
- Read-only references: `tests/integration/test_auth.py`, `tests/integration/test_tickets.py`, `app/api/deps.py`, `app/modules/usuarios/models/user.py`, `app/modules/usuarios/models/role.py`, `app/seed/demo_data.py`

**Interfaces:**
- Consumes the existing `app` fixture pattern, `get_session` override, demo accounts, `/api/v1/auth/login`, and SQLModel metadata.
- Produces failing HTTP tests for `/api/v1/users/advisors` before the route exists.

- [x] **Step 1: Write the failing tests**

Create an isolated SQLite `StaticPool` fixture that creates the existing metadata, seeds demo data, and overrides `get_session`. Add helper functions to log in demo users and to add additional `User` rows using the existing `ASESOR` role. Cover:

```python
def test_supervisor_gets_only_active_advisors_in_stable_name_order(advisor_client):
    # Add two active advisors, one inactive advisor, and rely on the seeded
    # client/supervisor rows to prove role and active filtering.
    token = login(advisor_client, "supervisor@demo.com")
    response = advisor_client.get(
        "/api/v1/users/advisors",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert [item["full_name"] for item in response.json()] == sorted(
        item["full_name"] for item in response.json()
    )
    assert all(item["role"] == "ASESOR" for item in response.json())
    assert set(response.json()[0]) == {"id", "full_name", "email", "role"}
    assert "password_hash" not in response.text
    assert "access_token" not in response.text
```

Also test that the inactive advisor, seeded client, and seeded supervisor are absent; clients and advisors receive 403; no authentication receives 401; a fixture with every advisor inactive returns 200 and `[]`; and the complete existing tickets test module remains runnable as a regression suite.

- [x] **Step 2: Run the focused tests and verify RED**

Run:

```bash
.\.venv\Scripts\python.exe -m pytest tests/integration/test_advisor_directory.py -q
```

Expected result: the new endpoint tests fail with `404 Not Found` because the users router is not registered yet, while the fixture and existing authentication setup succeed.

### Task 2: Implement the users directory through router → service → repository → schema/model

**Files:**
- Modify: `app/modules/usuarios/repositories/user_repository.py`
- Create: `app/modules/usuarios/services/user_service.py`
- Create: `app/modules/usuarios/api/user_router.py`
- Modify: `app/api/v1/router.py`
- Reuse: `app/modules/usuarios/schemas/auth.py` (`PublicUser`), `app/api/deps.py` (`require_roles`)

**Interfaces:**
- `UserRepository.list_active_advisors(session: Session) -> list[tuple[User, Role]]` performs one joined query filtered by `User.is_active` and `Role.name == "ASESOR"`, ordered by `User.full_name.asc(), User.id.asc()`.
- `UserService.list_advisors(session: Session) -> list[PublicUser]` maps the repository result to only the existing public schema fields.
- `GET /api/v1/users/advisors` requires `Annotated[AuthenticatedUser, Depends(require_roles("SUPERVISOR"))]`, uses `SessionDependency`, calls `UserService`, and returns `list[PublicUser]` with `response_model=list[PublicUser]`.

- [x] **Step 1: Add the minimal joined repository query**

Add `list_active_advisors` to `UserRepository` using SQLModel `select(User, Role)`, `.join(Role, User.role_id == Role.id)`, `.where(User.is_active, Role.name == "ASESOR")`, and `.order_by(User.full_name.asc(), User.id.asc())`. Return `list(session.exec(statement).all())` without changing existing repository methods.

- [x] **Step 2: Add the service mapping**

Create `UserService` with a repository dependency defaulting to `UserRepository`. Its `list_advisors` method should iterate over `(user, role)` rows and construct `PublicUser(id=user.id, full_name=user.full_name, email=user.email, role=role.name)`. It must not expose or copy `password_hash`, and it must not query roles individually.

- [x] **Step 3: Add and register the protected router**

Create an `APIRouter(prefix="/users", tags=["users"])`, a FastAPI service dependency, and this path operation:

```python
@router.get("/advisors", response_model=list[PublicUser])
def list_advisors(
    session: SessionDependency,
    _: Annotated[AuthenticatedUser, Depends(require_roles("SUPERVISOR"))],
    service: UserServiceDependency,
) -> list[PublicUser]:
    return service.list_advisors(session)
```

Register the router in `app/api/v1/router.py` under the existing `/api/v1` prefix. Do not add permission checks or SQL queries to the router beyond dependency wiring.

- [x] **Step 4: Run focused tests and verify GREEN**

Run:

```bash
.\.venv\Scripts\python.exe -m pytest tests/integration/test_advisor_directory.py -q
```

Expected result: all advisor-directory tests pass, including filtering, ordering, permissions, empty-list behavior, and safe response fields.

### Task 3: Document, review, validate, and commit

**Files:**
- Modify: `README.md`
- Modify: `docs/superpowers/plans/2026-09-13-advisor-directory-implementation.md` to mark completed steps after verification.
- Do not create migrations.

**Interfaces:**
- Documents the endpoint, permission, safe response contract, and supervisor usage example.
- Produces fresh evidence for tests, lint, formatting, migration state, Docker/MySQL health, API health/docs, scope, and repository cleanliness.

- [x] **Step 1: Update README**

Add an “Directorio de asesores” subsection under the ticket administration documentation stating that `GET /api/v1/users/advisors` requires a supervisor Bearer token, returns only active advisors ordered by name, and returns only `id`, `full_name`, `email`, and `role`. Include a curl example with `<supervisor_access_token>` and no real secret.

- [x] **Step 2: Run the complete validation suite**

From `C:\dev\sistema-gestion-tickets\backend`, run each command and require exit code 0:

```bash
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\ruff.exe check .
.\.venv\Scripts\ruff.exe format --check .
.\.venv\Scripts\python.exe -m alembic check
git diff --check
```

Also verify Docker/MySQL with `docker compose ps`, run `alembic upgrade head` if the database is not current, and verify `GET http://127.0.0.1:8000/api/v1/health` and `GET http://127.0.0.1:8000/docs` return HTTP 200. Confirm the OpenAPI document contains `/api/v1/users/advisors`.

- [x] **Step 3: Perform security and scope review**

Confirm the route requires the reusable supervisor role dependency, the repository excludes inactive/non-advisor roles in SQL, the response model filters sensitive fields, the query is not N+1, no frontend files changed, no migration was added, `.env` is ignored and untracked, and no actual secrets/tokens are present in tracked files. Inspect the final diff and run the full existing tickets regression module.

- [x] **Step 4: Complete the single final commit**

Stage only the plan, users repository/service/router, API router registration, advisor-directory tests, and README. Run:

```bash
git add docs/superpowers/plans/2026-09-13-advisor-directory-implementation.md app/modules/usuarios/repositories/user_repository.py app/modules/usuarios/services/user_service.py app/modules/usuarios/api/user_router.py app/api/v1/router.py tests/integration/test_advisor_directory.py README.md
git commit -m "feat(users): add advisor directory endpoint"
git status --short --branch
git log -1 --format="%H%n%s"
```

Expected result: the requested commit exists exactly once, the working tree is clean, `.env` remains ignored, and no frontend or external file is modified.

## Plan Self-Review

- Spec coverage: endpoint path, safe fields, active-advisor filtering, role exclusion, stable ordering, all required authorization outcomes, empty response, TDD, tickets regression, README, Docker/MySQL, health/docs, Alembic, secret checks, and exact commit are covered.
- Placeholder scan: every implementation step names a path, interface, or concrete command and contains no unfinished marker.
- Type consistency: repository rows are `tuple[User, Role]`, service returns `list[PublicUser]`, and the router uses the same public response model.
- Scope check: no model or migration change is planned; only the user directory endpoint and its tests/documentation are in scope.
