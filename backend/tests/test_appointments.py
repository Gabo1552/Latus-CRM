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


def _weekly(start="09:00", end="18:00"):
    return {
        "0": [{"start": start, "end": end}],
        "1": [{"start": start, "end": end}],
        "2": [{"start": start, "end": end}],
        "3": [{"start": start, "end": end}],
        "4": [{"start": start, "end": end}],
        "5": [],
        "6": [],
    }


def test_each_user_can_configure_own_availability(calendar_api):
    _, fake, client = calendar_api
    payload = {
        "enabled": True,
        "timezone": "America/Argentina/Buenos_Aires",
        "default_duration_minutes": 45,
        "buffer_minutes": 15,
        "weekly_schedule": _weekly("10:00", "17:00"),
    }
    response = client.patch(
        "/api/calendar/availability", headers=_headers("T-A1"), json=payload
    )
    assert response.status_code == 200, response.text
    assert response.json()["default_duration_minutes"] == 45
    assert response.json()["weekly_schedule"]["0"] == [{"start": "10:00", "end": "17:00"}]

    stored = _run(fake.users.find_one({"user_id": "u_agent_1"}))
    assert stored["calendar_settings"]["buffer_minutes"] == 15

    forbidden = client.get("/api/calendar/team-availability", headers=_headers("T-A1"))
    assert forbidden.status_code == 403
    team = client.get("/api/calendar/team-availability", headers=_headers("T-SUP"))
    assert team.status_code == 200
    assert any(item["user_id"] == "u_agent_1" for item in team.json())


def test_people_mode_enforces_person_hours_and_conflicts(calendar_api):
    _, fake, client = calendar_api
    _run(fake.bot_settings.insert_one({
        "_id": "default",
        "appointment_scheduling_enabled": True,
        "appointment_mode": "people",
        "appointment_timezone": "America/Argentina/Buenos_Aires",
    }))
    _run(fake.users.update_one({"user_id": "u_agent_1"}, {"$set": {"calendar_settings": {
        "enabled": True,
        "timezone": "America/Argentina/Buenos_Aires",
        "default_duration_minutes": 30,
        "buffer_minutes": 0,
        "weekly_schedule": _weekly(),
    }}}))

    first = client.post("/api/appointments", headers=_headers("T-A1"), json={
        "title": "Primera cita",
        "start_time": "2026-07-20T13:00:00+00:00",
        "end_time": "2026-07-20T13:30:00+00:00",
    })
    assert first.status_code == 200, first.text

    overlap = client.post("/api/appointments", headers=_headers("T-A1"), json={
        "title": "Cita superpuesta",
        "start_time": "2026-07-20T13:15:00+00:00",
        "end_time": "2026-07-20T13:45:00+00:00",
    })
    assert overlap.status_code == 409
    assert "otra cita" in overlap.json()["detail"]

    outside = client.post("/api/appointments", headers=_headers("T-A1"), json={
        "title": "Fuera de horario",
        "start_time": "2026-07-20T23:00:00+00:00",
        "end_time": "2026-07-20T23:30:00+00:00",
    })
    assert outside.status_code == 409
    assert "fuera de la disponibilidad" in outside.json()["detail"]

    changed_settings = _run(fake.users.find_one({"user_id": "u_agent_1"}))["calendar_settings"]
    changed_settings["enabled"] = False
    _run(fake.users.update_one({"user_id": "u_agent_1"}, {"$set": {"calendar_settings": changed_settings}}))
    # Non-scheduling edits remain possible even if availability changes later.
    renamed = client.patch(
        f"/api/appointments/{first.json()['id']}",
        headers=_headers("T-A1"),
        json={"title": "Cita renombrada"},
    )
    assert renamed.status_code == 200, renamed.text


def test_business_mode_enforces_service_duration_and_simultaneous_capacity(calendar_api):
    _, fake, client = calendar_api
    _run(fake.bot_settings.insert_one({
        "_id": "default",
        "appointment_scheduling_enabled": True,
        "appointment_mode": "business",
        "appointment_timezone": "America/Argentina/Buenos_Aires",
        "appointment_services": [{
            "id": "asesoria",
            "name": "Asesoría",
            "description": "Atención en el local",
            "active": True,
            "duration_minutes": 60,
            "max_concurrent": 2,
            "timezone": "America/Argentina/Buenos_Aires",
            "weekly_schedule": _weekly(),
        }],
    }))

    bodies = []
    for owner in ("u_agent_1", "u_agent_2"):
        response = client.post("/api/appointments", headers=_headers("T-ADMIN"), json={
            "title": f"Asesoría {owner}",
            "service_id": "asesoria",
            "assigned_to": owner,
            "start_time": "2026-07-20T13:00:00+00:00",
            "end_time": "2026-07-20T13:15:00+00:00",
        })
        assert response.status_code == 200, response.text
        bodies.append(response.json())

    assert bodies[0]["service_name"] == "Asesoría"
    assert bodies[0]["scheduling_mode"] == "business"
    assert bodies[0]["end_time"] == "2026-07-20T14:00:00+00:00"

    full = client.post("/api/appointments", headers=_headers("T-ADMIN"), json={
        "title": "Sin cupo",
        "service_id": "asesoria",
        "assigned_to": "u_admin",
        "start_time": "2026-07-20T13:30:00+00:00",
        "end_time": "2026-07-20T14:00:00+00:00",
    })
    assert full.status_code == 409
    assert "máximo de 2" in full.json()["detail"]


def test_business_mode_configuration_requires_an_active_service(calendar_api):
    _, _, client = calendar_api
    invalid = client.patch("/api/admin/bot-settings", headers=_headers("T-ADMIN"), json={
        "appointment_scheduling_enabled": True,
        "appointment_mode": "business",
        "appointment_services": [],
    })
    assert invalid.status_code == 400
    assert "servicio activo" in invalid.json()["detail"]

    valid = client.patch("/api/admin/bot-settings", headers=_headers("T-ADMIN"), json={
        "appointment_scheduling_enabled": True,
        "appointment_mode": "business",
        "appointment_timezone": "America/Argentina/Buenos_Aires",
        "appointment_services": [{
            "id": "evaluacion",
            "name": "Evaluación",
            "active": True,
            "duration_minutes": 45,
            "max_concurrent": 3,
            "weekly_schedule": _weekly("08:00", "16:00"),
        }],
    })
    assert valid.status_code == 200, valid.text
    service = valid.json()["appointment_services"][0]
    assert service["id"] == "evaluacion"
    assert service["max_concurrent"] == 3
