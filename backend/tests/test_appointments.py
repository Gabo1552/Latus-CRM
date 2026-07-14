"""Calendar ownership, manual editing and team filtering regression tests."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("DB_NAME", "latus_appointment_tests")
os.environ.setdefault("CORS_ORIGINS", "*")
os.environ.setdefault("APP_ENCRYPTION_KEY", "T9VemN99LrWMmb3im576htR6oNUwsyQdIhvFO9QuTI0=")

from tests.test_simulate_inbound import _FakeDB, _run  # noqa: E402


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _appointment(appt_id: str, assigned_to: str, *, created_by_bot: bool = False) -> dict:
    return {
        "id": appt_id,
        "title": f"Evento {appt_id}",
        "event_type": "appointment",
        "start_time": "2026-07-20T13:00:00+00:00",
        "end_time": "2026-07-20T13:30:00+00:00",
        "status": "scheduled",
        "assigned_to": assigned_to,
        "created_by_bot": created_by_bot,
        "created_at": "2026-07-14T12:00:00+00:00",
    }


@pytest.fixture
def calendar_api(monkeypatch):
    import server  # type: ignore

    fake = _FakeDB()
    monkeypatch.setattr(server, "db", fake)
    monkeypatch.setattr(server, "_start_scheduler", lambda: None, raising=False)

    users = [
        ("u_admin", "Admin", "admin", "T-ADMIN"),
        ("u_supervisor", "Supervisora", "supervisor", "T-SUP"),
        ("u_agent_1", "Agente Uno", "agent", "T-A1"),
        ("u_agent_2", "Agente Dos", "agent", "T-A2"),
    ]
    for user_id, name, role, token in users:
        _run(fake.users.insert_one({
            "user_id": user_id,
            "email": f"{user_id}@latus.test",
            "name": name,
            "role": role,
            "active": True,
            "auth_provider": "local",
            "created_at": "2026-01-01T00:00:00+00:00",
        }))
        _run(fake.user_sessions.insert_one({
            "user_id": user_id,
            "session_token": token,
            "expires_at": "2099-01-01T00:00:00+00:00",
            "created_at": "2026-01-01T00:00:00+00:00",
        }))

    return server, fake, TestClient(server.app)


def test_agent_creates_and_only_lists_own_events(calendar_api):
    _, fake, client = calendar_api
    _run(fake.appointments.insert_one(_appointment("appt_other", "u_agent_2")))

    response = client.post("/api/appointments", headers=_headers("T-A1"), json={
        "title": "Seguimiento manual",
        "event_type": "event",
        "start_time": "2026-07-20T14:00:00+00:00",
        "end_time": "2026-07-20T15:00:00+00:00",
    })
    assert response.status_code == 200, response.text
    created = response.json()
    assert created["assigned_to"] == "u_agent_1"
    assert created["created_by"] == "u_agent_1"
    assert created["created_by_bot"] is False

    listing = client.get(
        "/api/appointments?start=2026-07-01T00:00:00Z&end=2026-07-31T23:59:59Z",
        headers=_headers("T-A1"),
    )
    assert listing.status_code == 200
    assert {item["id"] for item in listing.json()} == {created["id"]}


def test_agent_cannot_create_edit_or_delete_events_for_another_user(calendar_api):
    _, fake, client = calendar_api
    _run(fake.appointments.insert_one(_appointment("appt_other", "u_agent_2")))

    create = client.post("/api/appointments", headers=_headers("T-A1"), json={
        "title": "Evento ajeno",
        "start_time": "2026-07-20T14:00:00+00:00",
        "end_time": "2026-07-20T15:00:00+00:00",
        "assigned_to": "u_agent_2",
    })
    assert create.status_code == 403

    update = client.patch(
        "/api/appointments/appt_other",
        headers=_headers("T-A1"),
        json={"title": "No autorizado"},
    )
    assert update.status_code == 403

    delete = client.delete("/api/appointments/appt_other", headers=_headers("T-A1"))
    assert delete.status_code == 403


def test_agent_can_modify_ai_event_assigned_to_them(calendar_api):
    _, fake, client = calendar_api
    _run(fake.appointments.insert_one(_appointment("appt_ai", "u_agent_1", created_by_bot=True)))

    response = client.patch("/api/appointments/appt_ai", headers=_headers("T-A1"), json={
        "title": "Cita ajustada por el agente",
        "location": "Videollamada",
        "start_time": "2026-07-20T13:30:00+00:00",
        "end_time": "2026-07-20T14:00:00+00:00",
    })
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["created_by_bot"] is True
    assert body["title"] == "Cita ajustada por el agente"
    assert body["location"] == "Videollamada"
    assert body["updated_by"] == "u_agent_1"


def test_supervisor_sees_combined_and_filtered_team_calendars(calendar_api):
    _, fake, client = calendar_api
    _run(fake.appointments.insert_one(_appointment("appt_a1", "u_agent_1")))
    second = _appointment("appt_a2", "u_agent_2")
    second["start_time"] = "2026-07-21T13:00:00+00:00"
    second["end_time"] = "2026-07-21T13:30:00+00:00"
    _run(fake.appointments.insert_one(second))

    combined = client.get(
        "/api/appointments?start=2026-07-01T00:00:00Z&end=2026-07-31T23:59:59Z",
        headers=_headers("T-SUP"),
    )
    assert combined.status_code == 200
    assert {item["id"] for item in combined.json()} == {"appt_a1", "appt_a2"}
    assert {item["assigned_user"]["name"] for item in combined.json()} == {"Agente Uno", "Agente Dos"}

    filtered = client.get(
        "/api/appointments?start=2026-07-01T00:00:00Z&end=2026-07-31T23:59:59Z&assigned_to=u_agent_2",
        headers=_headers("T-SUP"),
    )
    assert filtered.status_code == 200
    assert [item["id"] for item in filtered.json()] == ["appt_a2"]

    created_for_member = client.post("/api/appointments", headers=_headers("T-SUP"), json={
        "title": "Evento creado por supervisión",
        "event_type": "event",
        "start_time": "2026-07-22T15:00:00+00:00",
        "end_time": "2026-07-22T16:00:00+00:00",
        "assigned_to": "u_agent_1",
    })
    assert created_for_member.status_code == 200
    assert created_for_member.json()["assigned_to"] == "u_agent_1"

    edited = client.patch(
        "/api/appointments/appt_a2",
        headers=_headers("T-SUP"),
        json={"title": "Revisión de supervisión"},
    )
    assert edited.status_code == 200
    assert edited.json()["title"] == "Revisión de supervisión"


def test_rejects_invalid_time_range(calendar_api):
    _, _, client = calendar_api
    response = client.post("/api/appointments", headers=_headers("T-A1"), json={
        "title": "Horario inválido",
        "start_time": "2026-07-20T15:00:00+00:00",
        "end_time": "2026-07-20T14:00:00+00:00",
    })
    assert response.status_code == 400
    assert "hora de fin" in response.json()["detail"]
