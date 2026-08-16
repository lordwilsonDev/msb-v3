"""Software Factory API (spec §4.2.6, P3) — operator-gated.

POST /factory/run processes one issue end-to-end (classify → plan →
build in an isolated worktree → test → independent review → verify) and
returns the FactoryRun with its verdict + evidence chain. Builder: cli
(agent worker, default — fails loudly without a funded provider) or patch
(deterministic patch script, the reproducible path).
"""

from __future__ import annotations

import os
from typing import Dict

from fastapi import APIRouter, Depends, HTTPException

from msb_v3.api.auth import require_operator

router = APIRouter()

_MAX_LEN = 8000


@router.post("/run", dependencies=[Depends(require_operator)])
async def factory_run(body: Dict) -> Dict:
    from msb_v3.factory import Builder, CliAgentBuilder, PatchBuilder, SoftwareFactory
    from msb_v3.factory.models import Issue

    title = str(body.get("title") or "").strip()
    repo = str(body.get("repo") or "").strip()
    if not title or not repo:
        raise HTTPException(status_code=422, detail="title and repo are required")
    if len(title) > _MAX_LEN or len(str(body.get("body") or "")) > _MAX_LEN:
        raise HTTPException(status_code=422, detail=f"title/body exceed {_MAX_LEN} chars")
    if not os.path.isdir(repo):
        raise HTTPException(status_code=422, detail=f"repo is not a directory: {repo}")

    builder_name = str(body.get("builder") or "cli")
    builder: Builder
    if builder_name == "patch":
        script = str(body.get("patch_script") or "").strip()
        if not script or not os.path.isfile(script):
            raise HTTPException(status_code=422, detail="builder=patch requires patch_script (a file path)")
        builder = PatchBuilder(script)
    elif builder_name == "cli":
        builder = CliAgentBuilder()
    else:
        raise HTTPException(status_code=422, detail=f"unknown builder: {builder_name}")

    labels = body.get("labels")
    if not isinstance(labels, list):
        labels = []
    issue = Issue(title=title, body=str(body.get("body") or ""), repo=repo, labels=[str(x) for x in labels])

    # Optional diverse reviewer panel: reviewer_models (list of distinct model
    # ids). The builder!=reviewer invariant is enforced at build time (422 on
    # violation), so a worker can never review its own change.
    reviewer_panel = None
    reviewer_models = body.get("reviewer_models")
    if reviewer_models:
        if not isinstance(reviewer_models, list):
            raise HTTPException(status_code=422, detail="reviewer_models must be a list of model ids")
        from msb_v3.moie import build_diverse_reviewer_panel

        try:
            reviewer_panel = build_diverse_reviewer_panel(
                builder_model=builder.model, models=[str(m) for m in reviewer_models]
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    run = await SoftwareFactory(builder=builder, reviewer_panel=reviewer_panel).process_issue(issue, repo=repo)
    return {"ok": True, "run": run.as_dict()}
