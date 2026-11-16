"""Lobby API, mounted at ``/api/v1/lobby`` (T-0040, diff §4.1).

* ``GET /projects``: id, code, name of the lobby-visible projects, to **internal** users only
  (``is_internal``, T-0049). Anyone else gets 403 ``{"code": "LOBBY_INTERNAL_ONLY"}`` and no
  project data; the narrow read behind it returns nothing for them either.
* ``GET /projects/by-code/{code}``: the one project with exactly that code, lobby-visible or
  not, for every signed-in user, with a lookup ticket for the access request (T-0050). Unknown
  code: 404. Throttled per user and deployment-wide (429 with ``Retry-After``). A code never
  grants access by itself (TD-202).

Both mark the projects the caller already belongs to (``member``).
"""

from __future__ import annotations

from typing import Annotated, Any, Final

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse

from app.core.auth.access import CallerGrants
from app.core.auth.deps import CurrentPrincipal
from app.core.auth.lockout import LockedOutError
from app.core.auth.runtime import AuthRuntime, get_auth_runtime
from app.core.db import DbSession
from app.modules.lobby import service
from app.modules.lobby.schemas import LobbyCodeMatch, LobbyError, LobbyProject

router = APIRouter(tags=["lobby"])

INTERNAL_ONLY: Final = "LOBBY_INTERNAL_ONLY"
_INTERNAL_ONLY_RESPONSE: Final[dict[int | str, dict[str, Any]]] = {
    403: {"model": LobbyError, "description": f"Not internal staff (`code: {INTERNAL_ONLY}`)"}
}
_LOOKUP_RESPONSES: Final[dict[int | str, dict[str, Any]]] = {
    404: {"description": "No project has this code"},
    429: {"description": "Too many code lookups (`Retry-After`)"},
}


@router.get("/projects", response_model=list[LobbyProject], responses=_INTERNAL_ONLY_RESPONSE)
def lobby_projects(
    principal: CurrentPrincipal, grants: CallerGrants, session: DbSession
) -> list[LobbyProject] | JSONResponse:
    if not principal.user.is_internal:
        body = LobbyError(detail="The lobby list is for internal staff", code=INTERNAL_ONLY)
        return JSONResponse(body.model_dump(), status_code=status.HTTP_403_FORBIDDEN)
    return service.lobby_projects(session, grants)


@router.get(
    "/projects/by-code/{code}", response_model=LobbyCodeMatch, responses=_LOOKUP_RESPONSES
)
def find_by_code(
    code: str,
    principal: CurrentPrincipal,
    grants: CallerGrants,
    session: DbSession,
    runtime: Annotated[AuthRuntime, Depends(get_auth_runtime)],
) -> LobbyCodeMatch:
    try:
        service.throttle_code_lookup(runtime.store, principal.tenant_id, principal.user_id)
    except LockedOutError as exc:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Too many lookups",
            headers={"Retry-After": str(exc.retry_after)},
        ) from exc
    found = service.find_by_code(session, code, grants)
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not Found")
    ticket = service.issue_lookup_ticket(
        runtime.tokens,
        user_id=principal.user_id,
        tenant_id=principal.tenant_id,
        project_id=found.id,
    )
    return LobbyCodeMatch(
        **found.model_dump(), lookup_ticket=ticket.token, lookup_ticket_expires_at=ticket.expires_at
    )
