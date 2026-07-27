from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Iterator


@dataclass(frozen=True)
class Principal:
    user_id: int
    organization_id: int
    username: str
    permissions: frozenset[str]
    data_scope: str
    session_id: int | None = None
    auth_method: str = "local"

    def has_permission(self, permission: str) -> bool:
        return "*" in self.permissions or permission in self.permissions


@dataclass(frozen=True)
class RequestMetadata:
    request_id: str
    method: str
    path: str
    source_ip: str
    user_agent: str
    task_id: int | None = None


_principal: ContextVar[Principal | None] = ContextVar(
    "governance_principal",
    default=None,
)
_request: ContextVar[RequestMetadata | None] = ContextVar(
    "governance_request",
    default=None,
)


def current_principal() -> Principal | None:
    return _principal.get()


def current_request() -> RequestMetadata | None:
    return _request.get()


@contextmanager
def request_context(
    principal: Principal | None,
    metadata: RequestMetadata,
) -> Iterator[None]:
    principal_token = _principal.set(principal)
    request_token = _request.set(metadata)
    try:
        yield
    finally:
        _request.reset(request_token)
        _principal.reset(principal_token)


@contextmanager
def principal_context(principal: Principal) -> Iterator[None]:
    token = _principal.set(principal)
    try:
        yield
    finally:
        _principal.reset(token)
