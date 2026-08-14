from __future__ import annotations

import pytest

from msb_v3.vesta.models import ABind
from msb_v3.vesta.runtime import TaskLifecycleError, VestaTaskStore


def _to_executing(store: VestaTaskStore, task_id: str) -> None:
    for state in ("AUTHENTICATED", "PLANNED", "AUTHORIZED", "EXECUTING"):
        store.transition(task_id, state)


def test_task_lifecycle_is_persisted_and_rejects_invalid_jumps(tmp_path) -> None:
    store = VestaTaskStore(str(tmp_path / "tasks.db"))
    bind = ABind.create("session-1", ["model.inference"])
    store.create(bind)
    with pytest.raises(TaskLifecycleError, match="invalid transition"):
        store.transition(bind.task_id, "COMPLETED")

    _to_executing(store, bind.task_id)
    store.transition(bind.task_id, "VERIFYING")
    completed = store.transition(bind.task_id, "COMPLETED")
    assert completed["state"] == "COMPLETED"
    assert [item["to_state"] for item in completed["transitions"]] == [
        "RECEIVED",
        "AUTHENTICATED",
        "PLANNED",
        "AUTHORIZED",
        "EXECUTING",
        "VERIFYING",
        "COMPLETED",
    ]

    restarted = VestaTaskStore(str(tmp_path / "tasks.db"))
    assert restarted.get(bind.task_id)["state"] == "COMPLETED"


def test_restart_quarantines_in_flight_tasks(tmp_path) -> None:
    path = str(tmp_path / "tasks.db")
    store = VestaTaskStore(path)
    bind = ABind.create("session-1", ["model.inference"])
    store.create(bind)
    _to_executing(store, bind.task_id)

    restarted = VestaTaskStore(path)
    recovered = restarted.recover_incomplete()
    assert [task["task_id"] for task in recovered] == [bind.task_id]
    task = restarted.get(bind.task_id)
    assert task["state"] == "QUARANTINED"
    assert "restart" in task["last_error"]
