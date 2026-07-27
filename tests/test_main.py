import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

import app.main as main_module
from app.db import get_session
from app.main import app
from app.models import Series, Timeslot, User


@pytest.fixture
def db_engine():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    return engine


@pytest.fixture
def client(db_engine):
    def override_get_session():
        with Session(db_engine) as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session

    with Session(db_engine) as session:
        series = Series(
            season_id=1,
            name="GT3 Fixed",
            category="Sports Car",
            license_level="C",
            cadence_text="Races every hour at :00",
            session_times=["00:00", "01:00", "19:00", "20:30"],
        )
        session.add(series)
        session.commit()
        series_id = series.id

    test_client = TestClient(app)
    test_client.series_id = series_id
    yield test_client

    app.dependency_overrides.clear()


def _token_from_url(url: str) -> str:
    return str(url).rstrip("/").split("/")[-1]


def test_index_lists_series(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "GT3 Fixed" in response.text


def test_signup_edit_and_recovery_flow(client, db_engine, monkeypatch):
    # 1. New signup with one championship + one weekly timeslot.
    response = client.post(
        "/select",
        data={
            "name": "Alex",
            "email": "alex@example.com",
            "timezone": "America/New_York",
            "series_ids": [str(client.series_id)],
            "slot_series_id": [str(client.series_id)],
            "slot_day": ["2"],
            "slot_time": ["19:00"],
        },
    )
    assert response.status_code == 200
    assert "Hi Alex" in response.text
    token = _token_from_url(response.url)

    with Session(db_engine) as session:
        user = session.exec(select(User).where(User.token == token)).one()
        assert user.email == "alex@example.com"
        assert user.timezone == "America/New_York"
        timeslots = session.exec(select(Timeslot)).all()
        assert len(timeslots) == 1
        assert timeslots[0].day_of_week == 2

    # 2. Edit page pre-fills the existing checkbox + timeslot.
    edit_response = client.get(f"/u/{token}/edit")
    assert edit_response.status_code == 200
    assert f'value="{client.series_id}" class="series-checkbox"' in edit_response.text
    assert "checked" in edit_response.text
    assert 'value="19:00"' in edit_response.text

    # 3. Submitting the edit form replaces the timeslot rather than adding to it.
    update_response = client.post(
        f"/u/{token}/select",
        data={
            "name": "Alex",
            "email": "alex@example.com",
            "series_ids": [str(client.series_id)],
            "slot_series_id": [str(client.series_id)],
            "slot_day": ["5"],
            "slot_time": ["20:30"],
        },
    )
    assert update_response.status_code == 200

    with Session(db_engine) as session:
        timeslots = session.exec(select(Timeslot)).all()
        assert len(timeslots) == 1
        assert timeslots[0].day_of_week == 5

    # 4. Lost-link recovery emails the manage URL for that address.
    sent = []
    monkeypatch.setattr(
        main_module,
        "send_recovery_email",
        lambda settings, to_address, links: sent.append((to_address, links)),
    )
    lookup_response = client.post("/manage/lookup", data={"email": "alex@example.com"})
    assert lookup_response.status_code == 200
    assert "we've emailed the link" in lookup_response.text
    assert len(sent) == 1
    to_address, links = sent[0]
    assert to_address == "alex@example.com"
    assert links == [("Alex", f"http://localhost:8000/u/{token}")]


def test_signup_with_invalid_timezone_falls_back_to_utc(client, db_engine):
    response = client.post(
        "/select",
        data={
            "name": "Casey",
            "email": "casey@example.com",
            "timezone": "Not/A_Real_Zone",
            "series_ids": [str(client.series_id)],
        },
    )
    assert response.status_code == 200
    token = _token_from_url(response.url)

    with Session(db_engine) as session:
        user = session.exec(select(User).where(User.token == token)).one()
        assert user.timezone == "UTC"


def test_signup_with_no_selections_is_rejected(client):
    response = client.post(
        "/select",
        data={"name": "Jordan", "email": "jordan@example.com"},
    )
    assert response.status_code == 400


def test_recovery_lookup_for_unknown_email_gives_same_confirmation(client, monkeypatch):
    sent = []
    monkeypatch.setattr(
        main_module,
        "send_recovery_email",
        lambda settings, to_address, links: sent.append((to_address, links)),
    )
    response = client.post("/manage/lookup", data={"email": "nobody@example.com"})
    assert response.status_code == 200
    assert "we've emailed the link" in response.text
    assert sent == []
