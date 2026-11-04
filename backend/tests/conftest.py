from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI

from eagle.config import Settings
from eagle.executors.mock import MockExecutor
from eagle.main import create_app
from eagle.notify import Notifier
from eagle.worker import Worker
from tests.fakes import Clock, FakeGitHub, FakeSMTP, make_pr_repo

ADMIN_USER = "admin"
ADMIN_PASSWORD = "admin-password-for-tests"
PR = "https://github.com/acme/widgets/pull/42"


@pytest.fixture
def fake_github() -> FakeGitHub:
    fake = FakeGitHub()
    fake.add_pull()
    return fake


@pytest.fixture(autouse=True)
def reset_smtp():
    FakeSMTP.sent = []
    FakeSMTP.fail = False
    yield


@pytest.fixture
def pr_repo(tmp_path) -> dict:
    """Bare git repo with main and refs/pull/7/head, like GitHub."""
    return make_pr_repo(tmp_path)


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def mock_executor() -> MockExecutor:
    return MockExecutor()


@pytest.fixture
def notifier(app: FastAPI, fake_github: FakeGitHub) -> Notifier:
    n = Notifier(
        app.state.box,
        http_transport=fake_github.transport,
        smtp_class=FakeSMTP,
        smtp_ssl_class=FakeSMTP,
    )
    app.state.notifier = n
    return n


@pytest.fixture
def make_worker(app: FastAPI, settings: Settings, fake_github: FakeGitHub, notifier, clock):
    def _make(executor) -> Worker:
        return Worker(
            app.state.db.sessionmaker,
            settings,
            executor,
            app.state.box,
            http_transport=fake_github.transport,
            notifier=notifier,
            clock=clock,
        )

    return _make


@pytest.fixture
def worker(make_worker, mock_executor) -> Worker:
    return make_worker(mock_executor)


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        _env_file=None,
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        secret_key="test-secret-key",
        bootstrap_admin_username=ADMIN_USER,
        bootstrap_admin_password=ADMIN_PASSWORD,
    )


@pytest.fixture
async def app(settings: Settings) -> AsyncIterator[FastAPI]:
    application = create_app(settings)
    async with application.router.lifespan_context(application):
        yield application


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
async def make_client(app: FastAPI):
    """Factory for extra clients, each with its own cookie jar (one per user)."""
    clients: list[httpx.AsyncClient] = []

    async def _make() -> httpx.AsyncClient:
        c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
        clients.append(c)
        return c

    yield _make
    for c in clients:
        await c.aclose()


async def login(client: httpx.AsyncClient, username: str, password: str) -> None:
    resp = await client.post(
        "/api/v1/auth/login", json={"username": username, "password": password}
    )
    assert resp.status_code == 200, resp.text


@pytest.fixture
async def admin_client(client: httpx.AsyncClient) -> httpx.AsyncClient:
    await login(client, ADMIN_USER, ADMIN_PASSWORD)
    return client


@pytest.fixture
async def user_client(admin_client: httpx.AsyncClient, make_client):
    """Create a user with the given role and return a client logged in as them."""

    async def _create(username: str, role: str) -> httpx.AsyncClient:
        password = f"{username}-password-123"
        resp = await admin_client.post(
            "/api/v1/users", json={"username": username, "password": password, "role": role}
        )
        assert resp.status_code == 201, resp.text
        c = await make_client()
        await login(c, username, password)
        return c

    return _create
