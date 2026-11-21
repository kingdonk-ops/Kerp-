"""Asset hierarchy API, mounted at ``/api/v1/asset-management`` (T-0101).

The project is the one in the path: a non-member, another tenant's project or a deleted one
is 404, and the request session is then bound to it (``projects.bind_project``). A node or
level id of another project is 404 as well. Reads need ``V``; a new node ``C``; a move ``E``;
the breakdown template (level defs) is the Project Administrator's (``admin``).

* ``GET   /projects/{p}/level-defs``                    the breakdown template
* ``POST  /projects/{p}/level-defs``                    add a level
* ``PATCH /projects/{p}/level-defs/{id}``               change a level (additive once used)
* ``GET   /projects/{p}/nodes?parent_id=|subtree_of=``  children (roots) / a whole subtree
* ``POST  /projects/{p}/nodes``                         create a node
* ``GET   /projects/{p}/nodes/{id}``                    read a node
* ``POST  /projects/{p}/nodes/{id}/move``               move the node and its subtree
* ``GET   /projects/{p}/nodes/{id}/work-anchor``        the work anchor of the node's path

Rule violations (parent level, nested work anchor, code pattern, no work anchor) are 422; a
taken code or level key, or a frozen change to a level in use, is 409.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status

from app.core.auth.access import CallerGrants, Capability
from app.core.auth.deps import CurrentPrincipal
from app.core.db import DbSession
from app.modules.asset_management import service
from app.modules.asset_management.schemas import (
    LevelDefCreate,
    LevelDefRead,
    LevelDefUpdate,
    NodeCreate,
    NodeMove,
    NodeRead,
)
from app.modules.projects import service as projects

router = APIRouter(tags=["asset_management"])

LEVELS = "/projects/{project_id}/level-defs"
NODES = "/projects/{project_id}/nodes"
NODE = NODES + "/{node_id}"


@contextmanager
def rule_errors() -> Iterator[None]:
    """Map the service's domain errors to HTTP: rule violations 422, conflicts 409."""
    try:
        yield
    except (service.TreeRuleError, service.NoWorkAnchor) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    except (service.DuplicateCodeError, service.LevelInUseError) as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


@router.get(LEVELS, response_model=list[LevelDefRead])
def list_level_defs(
    project_id: uuid.UUID, principal: CurrentPrincipal, grants: CallerGrants, session: DbSession
) -> list[LevelDefRead]:
    projects.bind_project(session, principal, grants, project_id, require=Capability.VIEW)
    return [LevelDefRead.model_validate(level) for level in service.list_level_defs(session)]


@router.post(LEVELS, response_model=LevelDefRead, status_code=status.HTTP_201_CREATED)
def create_level_def(
    project_id: uuid.UUID,
    body: LevelDefCreate,
    principal: CurrentPrincipal,
    grants: CallerGrants,
    session: DbSession,
) -> LevelDefRead:
    projects.bind_project(session, principal, grants, project_id, require=Capability.ADMINISTER)
    with rule_errors():
        level = service.create_level_def(session, body, actor_id=principal.user_id)
    return LevelDefRead.model_validate(level)


@router.patch(LEVELS + "/{level_def_id}", response_model=LevelDefRead)
def update_level_def(
    project_id: uuid.UUID,
    level_def_id: uuid.UUID,
    body: LevelDefUpdate,
    principal: CurrentPrincipal,
    grants: CallerGrants,
    session: DbSession,
) -> LevelDefRead:
    projects.bind_project(session, principal, grants, project_id, require=Capability.ADMINISTER)
    with rule_errors():
        level = service.update_level_def(session, level_def_id, body, actor_id=principal.user_id)
    return LevelDefRead.model_validate(level)


@router.get(NODES, response_model=list[NodeRead])
def list_nodes(
    project_id: uuid.UUID,
    principal: CurrentPrincipal,
    grants: CallerGrants,
    session: DbSession,
    parent_id: uuid.UUID | None = None,
    subtree_of: uuid.UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=2000)] = 500,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[NodeRead]:
    projects.bind_project(session, principal, grants, project_id, require=Capability.VIEW)
    nodes = service.list_nodes(
        session, parent_id=parent_id, subtree_of=subtree_of, limit=limit, offset=offset
    )
    return [NodeRead.model_validate(node) for node in nodes]


@router.post(NODES, response_model=NodeRead, status_code=status.HTTP_201_CREATED)
def create_node(
    project_id: uuid.UUID,
    body: NodeCreate,
    principal: CurrentPrincipal,
    grants: CallerGrants,
    session: DbSession,
) -> NodeRead:
    projects.bind_project(session, principal, grants, project_id, require=Capability.CREATE)
    with rule_errors():
        node = service.create_node(session, body, actor_id=principal.user_id)
    return NodeRead.model_validate(node)


@router.get(NODE, response_model=NodeRead)
def read_node(
    project_id: uuid.UUID,
    node_id: uuid.UUID,
    principal: CurrentPrincipal,
    grants: CallerGrants,
    session: DbSession,
) -> NodeRead:
    projects.bind_project(session, principal, grants, project_id, require=Capability.VIEW)
    return NodeRead.model_validate(service.get_node(session, node_id))


@router.post(NODE + "/move", response_model=NodeRead)
def move_node(
    project_id: uuid.UUID,
    node_id: uuid.UUID,
    body: NodeMove,
    principal: CurrentPrincipal,
    grants: CallerGrants,
    session: DbSession,
) -> NodeRead:
    projects.bind_project(session, principal, grants, project_id, require=Capability.EDIT)
    with rule_errors():
        node = service.move_node(session, node_id, body.new_parent_id, actor_id=principal.user_id)
    return NodeRead.model_validate(node)


@router.get(NODE + "/work-anchor", response_model=NodeRead)
def read_work_anchor(
    project_id: uuid.UUID,
    node_id: uuid.UUID,
    principal: CurrentPrincipal,
    grants: CallerGrants,
    session: DbSession,
) -> NodeRead:
    """The work-anchor node of the node's path (itself or an ancestor); 422 when it has none."""
    projects.bind_project(session, principal, grants, project_id, require=Capability.VIEW)
    with rule_errors():
        return NodeRead.model_validate(service.work_anchor_for(session, node_id))
