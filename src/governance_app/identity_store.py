from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from sqlalchemy import (
    Column,
    Engine,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    create_engine,
    delete,
    func,
    insert,
    select,
    text,
    update,
)
from sqlalchemy.engine import URL
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import NullPool

from governance_app.config import AppConfig, RuntimeMode
from governance_app.request_context import Principal, RequestMetadata

identity_metadata = MetaData()

organizations = Table(
    "organizations",
    identity_metadata,
    Column("id", Integer, primary_key=True),
    Column("parent_id", Integer, ForeignKey("organizations.id")),
    Column("code", String(80), nullable=False, unique=True),
    Column("name", String(200), nullable=False),
    Column("domain_path", String(1000), nullable=False),
    Column("active", Integer, nullable=False),
    Column("created_at", Integer, nullable=False),
)

users = Table(
    "users",
    identity_metadata,
    Column("id", Integer, primary_key=True),
    Column(
        "organization_id",
        Integer,
        ForeignKey("organizations.id"),
        nullable=False,
    ),
    Column("username", String(120), nullable=False, unique=True),
    Column("display_name", String(200), nullable=False),
    Column("password_hash", String(500), nullable=False),
    Column("active", Integer, nullable=False),
    Column("failed_attempts", Integer, nullable=False),
    Column("locked_until", Integer),
    Column("created_at", Integer, nullable=False),
)

roles = Table(
    "roles",
    identity_metadata,
    Column("id", Integer, primary_key=True),
    Column("code", String(80), nullable=False, unique=True),
    Column("name", String(200), nullable=False),
    Column("data_scope", String(30), nullable=False),
)

role_permissions = Table(
    "role_permissions",
    identity_metadata,
    Column("role_id", Integer, ForeignKey("roles.id"), primary_key=True),
    Column("permission", String(120), primary_key=True),
)

user_roles = Table(
    "user_roles",
    identity_metadata,
    Column("user_id", Integer, ForeignKey("users.id"), primary_key=True),
    Column("role_id", Integer, ForeignKey("roles.id"), primary_key=True),
)

sessions = Table(
    "sessions",
    identity_metadata,
    Column("id", Integer, primary_key=True),
    Column("user_id", Integer, ForeignKey("users.id"), nullable=False),
    Column("token_hash", String(64), nullable=False, unique=True),
    Column("csrf_hash", String(64), nullable=False),
    Column("expires_at", Integer, nullable=False),
    Column("last_seen_at", Integer, nullable=False),
    Column("source_ip", String(100), nullable=False),
    Column("user_agent", String(500), nullable=False),
    Column("created_at", Integer, nullable=False),
)

batch_organizations = Table(
    "batch_organizations",
    identity_metadata,
    Column("batch_id", Integer, primary_key=True),
    Column(
        "organization_id",
        Integer,
        ForeignKey("organizations.id"),
        nullable=False,
    ),
    Column("created_by", Integer, ForeignKey("users.id"), nullable=False),
    Column("created_at", Integer, nullable=False),
)

request_audit_logs = Table(
    "request_audit_logs",
    identity_metadata,
    Column("id", Integer, primary_key=True),
    Column("request_id", String(64), nullable=False, unique=True),
    Column("user_id", Integer),
    Column("organization_id", Integer),
    Column("method", String(10), nullable=False),
    Column("path", String(1000), nullable=False),
    Column("status", Integer, nullable=False),
    Column("source_ip", String(100), nullable=False),
    Column("user_agent", String(500), nullable=False),
    Column("task_id", Integer),
    Column("duration_ms", Integer, nullable=False),
    Column("created_at", Integer, nullable=False),
)

background_tasks = Table(
    "background_tasks",
    identity_metadata,
    Column("id", Integer, primary_key=True),
    Column(
        "organization_id",
        Integer,
        ForeignKey("organizations.id"),
        nullable=False,
    ),
    Column("user_id", Integer, ForeignKey("users.id"), nullable=False),
    Column("kind", String(80), nullable=False),
    Column("status", String(30), nullable=False),
    Column("payload_json", String, nullable=False),
    Column("result_json", String),
    Column("error", String),
    Column("progress", Integer, nullable=False),
    Column("attempts", Integer, nullable=False),
    Column("max_attempts", Integer, nullable=False),
    Column("idempotency_key", String(200), nullable=False),
    Column("created_at", Integer, nullable=False),
    Column("started_at", Integer),
    Column("finished_at", Integer),
    UniqueConstraint(
        "organization_id",
        "kind",
        "idempotency_key",
        name="uq_background_task_idempotency",
    ),
)

Index(
    "idx_users_organization",
    users.c.organization_id,
    users.c.active,
)
Index(
    "idx_sessions_expiry",
    sessions.c.expires_at,
)
Index(
    "idx_request_audit_created",
    request_audit_logs.c.created_at,
)
Index(
    "idx_tasks_org_status",
    background_tasks.c.organization_id,
    background_tasks.c.status,
    background_tasks.c.created_at,
)

ALL_PERMISSIONS = frozenset(
    {
        "dashboard.read",
        "batch.manage",
        "import.run",
        "audit.run",
        "issue.manage",
        "analysis.run",
        "report.export",
        "identity.manage",
        "system.admin",
        "task.manage",
    }
)

CITY_ADMIN_PERMISSIONS = frozenset({
    "dashboard.read", "identity.manage", "issue.manage", "report.export",
})

ROLE_DEFINITIONS = {
    "platform_admin": ("平台管理员", "organization", {"system.admin"}),
    "province_admin": (
        "省级业务管理员", "all", ALL_PERMISSIONS - {"system.admin"},
    ),
    "organization_admin": (
        "市州管理员（兼容）", "organization",
        CITY_ADMIN_PERMISSIONS,
    ),
    "city_admin": (
        "市州管理员", "organization",
        CITY_ADMIN_PERMISSIONS,
    ),
    "auditor": (
        "稽核人员",
        "organization",
        {
            "dashboard.read",
            "audit.run",
            "analysis.run",
            "report.export",
            "task.manage",
        },
    ),
    "operator": (
        "整改人员",
        "organization",
        {
            "dashboard.read",
            "import.run",
            "issue.manage",
            "report.export",
            "task.manage",
        },
    ),
}

_DUMMY_PASSWORD_HASH = (
    "pbkdf2_sha256$240000$"
    "00000000000000000000000000000000$"
    "20c7e9b4174f20376cc9f2692750c224"
    "f0f545b6244b02f62dd5a96b0df6f24"
)


@dataclass(frozen=True)
class SessionGrant:
    principal: Principal
    token: str
    csrf_token: str
    expires_at: int


@dataclass(frozen=True)
class TaskRecord:
    id: int
    organization_id: int
    user_id: int
    kind: str
    status: str
    payload: dict[str, Any]
    result: dict[str, Any] | None
    error: str | None
    progress: int
    attempts: int
    max_attempts: int
    idempotency_key: str


class AuthenticationError(ValueError):
    pass


class AuthorizationError(PermissionError):
    pass


class IdentityStore:
    def __init__(
        self,
        config: AppConfig,
        *,
        engine: Engine | None = None,
    ) -> None:
        self._config = config
        self._engine = engine or _identity_engine(config)

    def initialize(self) -> None:
        identity_metadata.create_all(self._engine)

    def ping(self) -> None:
        with self._engine.connect() as connection:
            connection.execute(text("select 1")).scalar_one()

    def bootstrap(
        self,
        *,
        username: str,
        password: str,
    ) -> None:
        if len(password) < 12:
            raise ValueError("bootstrap admin password must be at least 12 characters")
        now = int(time.time())
        with self._engine.begin() as connection:
            organization_id = connection.execute(
                select(organizations.c.id).where(
                    organizations.c.code == "province"
                )
            ).scalar_one_or_none()
            if organization_id is None:
                result = connection.execute(
                    insert(organizations).values(
                        code="province",
                        name="省公司",
                        domain_path="/province/",
                        active=1,
                        created_at=now,
                    )
                )
                organization_id = _primary_key(result)
            legacy_roots = connection.execute(select(
                organizations.c.id, organizations.c.domain_path,
            ).where(
                organizations.c.parent_id.is_(None),
                organizations.c.code.not_in(("province", "platform")),
            )).mappings().all()
            for root in legacy_roots:
                old_path = str(root["domain_path"])
                descendants = connection.execute(select(
                    organizations.c.id, organizations.c.domain_path,
                ).where(func.substr(organizations.c.domain_path, 1, len(old_path)) == old_path)).mappings().all()
                for descendant in descendants:
                    connection.execute(update(organizations).where(
                        organizations.c.id == descendant["id"]
                    ).values(domain_path="/province/" + str(descendant["domain_path"])[len("/"):]))
                connection.execute(update(organizations).where(
                    organizations.c.id == root["id"]
                ).values(parent_id=organization_id))
            legacy_platform_id = connection.execute(select(organizations.c.id).where(
                organizations.c.code == "platform"
            )).scalar_one_or_none()
            if legacy_platform_id is not None:
                connection.execute(update(batch_organizations).where(
                    batch_organizations.c.organization_id == legacy_platform_id
                ).values(organization_id=organization_id))
            role_ids: dict[str, int] = {}
            for code, (name, data_scope, permissions) in ROLE_DEFINITIONS.items():
                role_id = connection.execute(
                    select(roles.c.id).where(roles.c.code == code)
                ).scalar_one_or_none()
                if role_id is None:
                    role_id = _primary_key(
                        connection.execute(
                            insert(roles).values(
                                code=code,
                                name=name,
                                data_scope=data_scope,
                            )
                        )
                    )
                role_ids[code] = role_id
                connection.execute(
                    update(roles).where(roles.c.id == role_id).values(
                        name=name, data_scope=data_scope,
                    )
                )
                existing_permissions = set(
                    connection.execute(
                        select(role_permissions.c.permission).where(
                            role_permissions.c.role_id == role_id
                        )
                    ).scalars()
                )
                for permission in permissions - existing_permissions:
                    connection.execute(
                        insert(role_permissions).values(
                            role_id=role_id,
                            permission=permission,
                        )
                    )
                for permission in existing_permissions - permissions:
                    connection.execute(
                        delete(role_permissions).where(
                            role_permissions.c.role_id == role_id,
                            role_permissions.c.permission == permission,
                        )
                    )
            user_id = connection.execute(
                select(users.c.id).where(users.c.username == username)
            ).scalar_one_or_none()
            if user_id is None:
                user_id = _primary_key(
                    connection.execute(
                        insert(users).values(
                            organization_id=organization_id,
                            username=username,
                            display_name="省级业务管理员",
                            password_hash=hash_password(password),
                            active=1,
                            failed_attempts=0,
                            created_at=now,
                        )
                    )
                )
            else:
                connection.execute(
                    update(users).where(users.c.id == user_id).values(
                        organization_id=organization_id,
                    )
                )
                connection.execute(delete(user_roles).where(user_roles.c.user_id == user_id))
            connection.execute(insert(user_roles).values(
                user_id=user_id, role_id=role_ids["province_admin"],
            ))
            unclaimed_batch_ids = connection.execute(
                text(
                    "select id from import_batches "
                    "where id not in (select batch_id "
                    "from batch_organizations)"
                )
            ).scalars()
            for batch_id in unclaimed_batch_ids:
                connection.execute(
                    insert(batch_organizations).values(
                        batch_id=int(batch_id),
                        organization_id=organization_id,
                        created_by=user_id,
                        created_at=now,
                    )
                )

    def provision_platform_admin(self, *, username: str, password: str) -> None:
        if not username.strip() or len(password) < 12:
            raise ValueError("platform admin username and password are required (12+ characters)")
        with self._engine.begin() as connection:
            if connection.execute(select(users.c.id).where(
                users.c.username == username.strip()
            )).scalar_one_or_none() is not None:
                raise ValueError("username already exists")
            platform_id = connection.execute(select(organizations.c.id).where(
                organizations.c.code == "platform"
            )).scalar_one_or_none()
            if platform_id is None:
                platform_id = _primary_key(connection.execute(insert(organizations).values(
                    code="platform", name="平台管理组织", domain_path="/platform/",
                    active=1, created_at=int(time.time()),
                )))
            role_id = connection.execute(select(roles.c.id).where(
                roles.c.code == "platform_admin"
            )).scalar_one()
            user_id = _primary_key(connection.execute(insert(users).values(
                organization_id=platform_id, username=username.strip(),
                display_name="平台管理员", password_hash=hash_password(password),
                active=1, failed_attempts=0, created_at=int(time.time()),
            )))
            connection.execute(insert(user_roles).values(user_id=user_id, role_id=role_id))

    def create_organization(
        self,
        *,
        code: str,
        name: str,
        parent_id: int | None = None,
        actor: Principal | None = None,
    ) -> int:
        code = code.strip()
        name = name.strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{1,79}", code) or not name:
            raise ValueError("invalid organization code or name")
        now = int(time.time())
        with self._engine.begin() as connection:
            if parent_id is None:
                parent_id = connection.execute(select(organizations.c.id).where(
                    organizations.c.code == "province"
                )).scalar_one()
            parent = connection.execute(select(organizations).where(
                organizations.c.id == parent_id, organizations.c.active == 1
            )).mappings().one_or_none()
            if (parent is None or not str(parent["domain_path"]).startswith("/province/")
                    or str(parent["domain_path"]).count("/") not in (2, 3)):
                raise ValueError("parent must be a province or city organization")
            if actor is not None and "province_admin" not in actor.role_codes:
                raise AuthorizationError("only province business administrators can manage organizations")
            try:
                result = connection.execute(insert(organizations).values(
                    parent_id=parent_id, code=code, name=name,
                    domain_path=f"{parent['domain_path']}{code}/",
                    active=1, created_at=now,
                ))
            except IntegrityError as exc:
                raise ValueError("organization code already exists") from exc
            return _primary_key(result)

    def organization(self, organization_id: int) -> dict[str, Any] | None:
        with self._engine.connect() as connection:
            row = connection.execute(select(organizations).where(
                organizations.c.id == organization_id,
                organizations.c.active == 1,
            )).mappings().one_or_none()
        return None if row is None else dict(row)

    def correction_reviewer_organization_id(self, organization_id: int) -> int | None:
        organization = self.organization(organization_id)
        if organization is None:
            return None
        path = str(organization["domain_path"])
        if path == "/province/":
            return int(organization["id"])
        if path.startswith("/province/") and path.count("/") in (3, 4):
            return int(organization["parent_id"])
        return None

    def update_organization(
        self, *, actor: Principal, organization_id: int,
        name: str, parent_id: int,
    ) -> None:
        if "province_admin" not in actor.role_codes:
            raise AuthorizationError("only province business administrators can manage organizations")
        name = name.strip()
        if not name:
            raise ValueError("organization name is required")
        with self._engine.begin() as connection:
            target = connection.execute(select(organizations).where(
                organizations.c.id == organization_id,
                organizations.c.active == 1,
            )).mappings().one_or_none()
            parent = connection.execute(select(organizations).where(
                organizations.c.id == parent_id,
                organizations.c.active == 1,
            )).mappings().one_or_none()
            if (target is None or parent is None or not str(target["domain_path"]).startswith("/province/")
                    or not str(parent["domain_path"]).startswith("/province/")
                    or target["code"] in ("province", "platform")):
                raise ValueError("organization not found")
            old_path = str(target["domain_path"])
            parent_path = str(parent["domain_path"])
            expected_parent_depth = 2 if old_path.count("/") == 3 else 3
            if parent_path.count("/") != expected_parent_depth or parent_path.startswith(old_path):
                raise ValueError("invalid organization parent")
            new_path = f"{parent_path}{target['code']}/"
            descendants = connection.execute(select(
                organizations.c.id, organizations.c.domain_path,
            ).where(func.substr(organizations.c.domain_path, 1, len(old_path)) == old_path)).mappings()
            for descendant in descendants:
                connection.execute(update(organizations).where(
                    organizations.c.id == descendant["id"]
                ).values(domain_path=new_path + str(descendant["domain_path"])[len(old_path):]))
            connection.execute(update(organizations).where(
                organizations.c.id == organization_id
            ).values(name=name, parent_id=parent_id))

    def create_user(
        self,
        *,
        actor: Principal,
        organization_id: int,
        username: str,
        display_name: str,
        password: str,
        role_codes: list[str],
    ) -> int:
        username = username.strip()
        display_name = display_name.strip() or username
        if not username:
            raise ValueError("username is required")
        if len(password) < 12:
            raise ValueError("password must be at least 12 characters")
        if not role_codes:
            raise ValueError("at least one role is required")
        now = int(time.time())
        with self._engine.begin() as connection:
            self._ensure_manageable_organization(connection, actor, organization_id)
            target = connection.execute(select(organizations.c.domain_path).where(
                organizations.c.id == organization_id,
            )).scalar_one()
            depth = str(target).count("/")
            allowed = ({"province_admin", "auditor", "operator"}
                       if depth == 2 else {"city_admin", "organization_admin", "auditor", "operator"}
                       if depth == 3 else {"operator", "auditor"})
            if not set(role_codes) <= allowed:
                raise ValueError("roles are not valid for the target organization")
            if actor.role_codes & {"city_admin", "organization_admin"} and not set(role_codes) <= {"operator", "auditor"}:
                raise AuthorizationError("city administrators cannot grant administrator roles")
            role_rows = connection.execute(
                select(roles.c.id, roles.c.code).where(
                    roles.c.code.in_(role_codes)
                )
            ).mappings()
            role_map = {str(row["code"]): int(row["id"]) for row in role_rows}
            missing = set(role_codes) - role_map.keys()
            if missing:
                raise ValueError(f"unknown roles: {', '.join(sorted(missing))}")
            user_id = _primary_key(
                connection.execute(
                    insert(users).values(
                        organization_id=organization_id,
                        username=username,
                        display_name=display_name,
                        password_hash=hash_password(password),
                        active=1,
                        failed_attempts=0,
                        created_at=now,
                    )
                )
            )
            for role_id in role_map.values():
                connection.execute(
                    insert(user_roles).values(
                        user_id=user_id,
                        role_id=role_id,
                    )
                )
            return user_id

    def list_organizations(
        self,
        principal: Principal,
    ) -> list[dict[str, Any]]:
        statement = select(
            organizations.c.id,
            organizations.c.parent_id,
            organizations.c.code,
            organizations.c.name,
            organizations.c.domain_path,
            organizations.c.active,
        ).order_by(organizations.c.domain_path)
        with self._engine.connect() as connection:
            if principal.data_scope == "all":
                statement = statement.where(func.substr(organizations.c.domain_path, 1, 10) == "/province/")
            else:
                own_path = self._organization_path(connection, principal.organization_id)
                statement = statement.where(
                    func.substr(organizations.c.domain_path, 1, len(own_path)) == own_path
                )
            return [
                dict(row)
                for row in connection.execute(statement).mappings()
            ]

    def list_users(
        self,
        principal: Principal,
    ) -> list[dict[str, Any]]:
        statement = (
            select(
                users.c.id,
                users.c.organization_id,
                users.c.username,
                users.c.display_name,
                users.c.active,
                organizations.c.name.label("organization_name"),
            )
            .select_from(
                users.join(
                    organizations,
                    organizations.c.id == users.c.organization_id,
                )
            )
            .order_by(users.c.id)
        )
        with self._engine.connect() as connection:
            if principal.data_scope == "all":
                statement = statement.where(func.substr(organizations.c.domain_path, 1, 10) == "/province/")
            else:
                own_path = self._organization_path(connection, principal.organization_id)
                statement = statement.where(func.substr(organizations.c.domain_path, 1, len(own_path)) == own_path)
            payloads = [
                dict(row)
                for row in connection.execute(statement).mappings()
            ]
            for payload in payloads:
                payload["roles"] = list(
                    connection.execute(
                        select(roles.c.code)
                        .select_from(
                            user_roles.join(
                                roles,
                                roles.c.id == user_roles.c.role_id,
                            )
                        )
                        .where(
                            user_roles.c.user_id == payload["id"]
                        )
                        .order_by(roles.c.code)
                    ).scalars()
                )
            return payloads

    def set_user_active(
        self,
        actor: Principal,
        user_id: int,
        *,
        active: bool,
    ) -> None:
        with self._engine.begin() as connection:
            self._ensure_user_in_scope(connection, actor, user_id)
            connection.execute(
                update(users)
                .where(users.c.id == user_id)
                .values(active=1 if active else 0)
            )
            if not active:
                connection.execute(
                    delete(sessions).where(sessions.c.user_id == user_id)
                )

    def reset_user_password(
        self,
        actor: Principal,
        user_id: int,
        *,
        password: str,
    ) -> None:
        if len(password) < 12:
            raise ValueError("password must be at least 12 characters")
        with self._engine.begin() as connection:
            self._ensure_user_in_scope(connection, actor, user_id)
            connection.execute(
                update(users)
                .where(users.c.id == user_id)
                .values(
                    password_hash=hash_password(password),
                    failed_attempts=0,
                    locked_until=None,
                )
            )
            connection.execute(
                delete(sessions).where(sessions.c.user_id == user_id)
            )

    def _ensure_user_in_scope(
        self,
        connection,
        actor: Principal,
        user_id: int,
    ) -> None:
        organization_id = connection.execute(
            select(users.c.organization_id).where(users.c.id == user_id)
        ).scalar_one_or_none()
        if organization_id is None or actor.user_id == user_id:
            raise AuthorizationError("user is outside your management scope")
        self._ensure_manageable_organization(connection, actor, int(organization_id))
        target_roles = set(connection.execute(select(roles.c.code).select_from(
            user_roles.join(roles, roles.c.id == user_roles.c.role_id)
        ).where(user_roles.c.user_id == user_id)).scalars())
        if actor.role_codes & {"city_admin", "organization_admin"} and target_roles - {"operator", "auditor"}:
            raise AuthorizationError("cannot manage administrator accounts")

    def _organization_path(self, connection, organization_id: int) -> str:
        path = connection.execute(select(organizations.c.domain_path).where(
            organizations.c.id == organization_id, organizations.c.active == 1,
        )).scalar_one_or_none()
        if path is None:
            raise ValueError("organization not found")
        return str(path)

    def _ensure_manageable_organization(self, connection, actor: Principal, organization_id: int) -> None:
        target_path = self._organization_path(connection, organization_id)
        if "province_admin" in actor.role_codes:
            if target_path.startswith("/province/"):
                return
        if actor.role_codes & {"city_admin", "organization_admin"}:
            own_path = self._organization_path(connection, actor.organization_id)
            if own_path.count("/") == 3 and target_path.startswith(own_path):
                return
        raise AuthorizationError("organization is outside your management scope")

    def authenticate(
        self,
        *,
        username: str,
        password: str,
        source_ip: str,
        user_agent: str,
        ttl_seconds: int,
    ) -> SessionGrant:
        now = int(time.time())
        authentication_error: str | None = None
        grant: SessionGrant | None = None
        with self._engine.begin() as connection:
            row = connection.execute(
                select(users).where(users.c.username == username.strip())
            ).mappings().one_or_none()
            password_hash = (
                _DUMMY_PASSWORD_HASH
                if row is None
                else str(row["password_hash"])
            )
            password_ok = verify_password(password, password_hash)
            if row is None or not int(row["active"]):
                authentication_error = "用户名或密码错误"
            elif int(row["locked_until"] or 0) > now:
                authentication_error = "账号暂时锁定，请稍后重试"
            elif not password_ok:
                failed_attempts = int(row["failed_attempts"] or 0) + 1
                connection.execute(
                    update(users)
                    .where(users.c.id == row["id"])
                    .values(
                        failed_attempts=failed_attempts,
                        locked_until=(
                            now + 15 * 60
                            if failed_attempts >= 5
                            else None
                        ),
                    )
                )
                authentication_error = "用户名或密码错误"
            else:
                connection.execute(
                    update(users)
                    .where(users.c.id == row["id"])
                    .values(failed_attempts=0, locked_until=None)
                )
                token = secrets.token_urlsafe(32)
                csrf_token = secrets.token_urlsafe(32)
                expires_at = now + ttl_seconds
                session_id = _primary_key(
                    connection.execute(
                        insert(sessions).values(
                            user_id=row["id"],
                            token_hash=_token_hash(token),
                            csrf_hash=_token_hash(csrf_token),
                            expires_at=expires_at,
                            last_seen_at=now,
                            source_ip=source_ip,
                            user_agent=user_agent[:500],
                            created_at=now,
                        )
                    )
                )
                principal = self._principal_for_user(
                    connection,
                    int(row["id"]),
                    session_id=session_id,
                    auth_method="cookie",
                )
                grant = SessionGrant(
                    principal=principal,
                    token=token,
                    csrf_token=csrf_token,
                    expires_at=expires_at,
                )
        if authentication_error:
            raise AuthenticationError(authentication_error)
        assert grant is not None
        return grant

    def session_principal(
        self,
        token: str,
        *,
        auth_method: str,
    ) -> Principal | None:
        now = int(time.time())
        token_hash = _token_hash(token)
        with self._engine.begin() as connection:
            row = connection.execute(
                select(sessions.c.id, sessions.c.user_id).where(
                    sessions.c.token_hash == token_hash,
                    sessions.c.expires_at > now,
                )
            ).mappings().one_or_none()
            if row is None:
                return None
            connection.execute(
                update(sessions)
                .where(sessions.c.id == row["id"])
                .values(last_seen_at=now)
            )
            return self._principal_for_user(
                connection,
                int(row["user_id"]),
                session_id=int(row["id"]),
                auth_method=auth_method,
            )

    def validate_csrf(
        self,
        session_id: int,
        csrf_token: str,
    ) -> bool:
        with self._engine.connect() as connection:
            expected = connection.execute(
                select(sessions.c.csrf_hash).where(
                    sessions.c.id == session_id
                )
            ).scalar_one_or_none()
        return bool(
            expected
            and hmac.compare_digest(str(expected), _token_hash(csrf_token))
        )

    def logout(self, session_id: int) -> None:
        with self._engine.begin() as connection:
            connection.execute(
                delete(sessions).where(sessions.c.id == session_id)
            )

    def claim_batch(
        self,
        batch_id: int,
        principal: Principal,
    ) -> None:
        now = int(time.time())
        with self._engine.begin() as connection:
            existing = connection.execute(
                select(batch_organizations.c.batch_id).where(
                    batch_organizations.c.batch_id == batch_id
                )
            ).first()
            if existing is None:
                connection.execute(
                    insert(batch_organizations).values(
                        batch_id=batch_id,
                        organization_id=principal.organization_id,
                        created_by=principal.user_id,
                        created_at=now,
                    )
                )

    def can_access_batch(
        self,
        principal: Principal,
        batch_id: int,
    ) -> bool:
        if principal.data_scope == "all":
            return True
        with self._engine.connect() as connection:
            owner = connection.execute(
                select(batch_organizations.c.organization_id).where(
                    batch_organizations.c.batch_id == batch_id
                )
            ).scalar_one_or_none()
        if owner == principal.organization_id:
            return True
        with self._engine.connect() as connection:
            province_id = connection.execute(select(organizations.c.id).where(
                organizations.c.code == "province"
            )).scalar_one_or_none()
            path = self._organization_path(connection, principal.organization_id)
        return owner == province_id and path.startswith("/province/")

    def site_jurisdictions(self, principal: Principal) -> tuple[tuple[str, str], ...]:
        """Only unambiguous, active city/county pairs may leave the province queue."""
        if principal.data_scope == "all":
            return ()
        with self._engine.connect() as connection:
            own_path = self._organization_path(connection, principal.organization_id)
            if not own_path.startswith("/province/"):
                return ()
            rows = connection.execute(select(
                organizations.c.name, organizations.c.domain_path,
            ).where(organizations.c.active == 1,
                    func.substr(organizations.c.domain_path, 1, 10) == "/province/")
            ).mappings().all()
        cities = [(str(row["name"]), str(row["domain_path"])) for row in rows
                  if str(row["domain_path"]).count("/") == 3]
        counties = [(str(row["name"]), str(row["domain_path"])) for row in rows
                    if str(row["domain_path"]).count("/") == 4]
        pairs: list[tuple[str, str]] = []
        for city_name, city_path in cities:
            if sum(name == city_name for name, _ in cities) != 1:
                continue
            for county_name, county_path in counties:
                if not county_path.startswith(city_path):
                    continue
                if sum(name == county_name and path.startswith(city_path)
                       for name, path in counties) != 1:
                    continue
                if city_path.startswith(own_path) or county_path == own_path:
                    pairs.append((city_name, county_name))
        return tuple(pairs)

    def is_valid_site_jurisdiction(self, city: str, district: str) -> bool:
        with self._engine.connect() as connection:
            matches = connection.execute(select(organizations.c.domain_path)
                .where(organizations.c.name == city, organizations.c.active == 1)).scalars().all()
            city_paths = [str(path) for path in matches
                          if str(path).startswith("/province/") and str(path).count("/") == 3]
            if len(city_paths) != 1:
                return False
            county_paths = connection.execute(select(organizations.c.domain_path)
                .where(organizations.c.name == district, organizations.c.active == 1)).scalars().all()
            return sum(str(path).startswith(city_paths[0]) and str(path).count("/") == 4
                       for path in county_paths) == 1

    def can_access_issue(
        self,
        principal: Principal,
        issue_code: str,
    ) -> bool:
        if principal.data_scope == "all":
            return True
        with self._engine.connect() as connection:
            issue = connection.execute(
                text(
                    "select batch_id, ledger_type, city, district from issues "
                    "where issue_code = :issue_code"
                ),
                {"issue_code": issue_code},
            ).mappings().one_or_none()
        return bool(
            issue is not None
            and issue["ledger_type"] == "site"
            and (issue["city"], issue["district"]) in self.site_jurisdictions(principal)
            and self.can_access_batch(principal, int(issue["batch_id"]))
        )

    def allowed_batch_ids(
        self,
        principal: Principal,
    ) -> set[int] | None:
        if principal.data_scope == "all":
            return None
        with self._engine.connect() as connection:
            path = self._organization_path(connection, principal.organization_id)
            province_id = connection.execute(select(organizations.c.id).where(
                organizations.c.code == "province"
            )).scalar_one_or_none()
            owners = [principal.organization_id]
            if path.startswith("/province/") and province_id is not None:
                owners.append(province_id)
            return {
                int(value)
                for value in connection.execute(
                    select(batch_organizations.c.batch_id).where(
                        batch_organizations.c.organization_id
                        .in_(owners)
                    )
                ).scalars()
            }

    def record_request(
        self,
        metadata: RequestMetadata,
        principal: Principal | None,
        *,
        status: int,
        duration_ms: int,
    ) -> None:
        with self._engine.begin() as connection:
            connection.execute(
                insert(request_audit_logs).values(
                    request_id=metadata.request_id,
                    user_id=None if principal is None else principal.user_id,
                    organization_id=(
                        None
                        if principal is None
                        else principal.organization_id
                    ),
                    method=metadata.method,
                    path=metadata.path,
                    status=status,
                    source_ip=metadata.source_ip,
                    user_agent=metadata.user_agent[:500],
                    task_id=metadata.task_id,
                    duration_ms=duration_ms,
                    created_at=int(time.time()),
                )
            )

    def request_logs(
        self,
        principal: Principal,
        *,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        statement = select(request_audit_logs).order_by(
            request_audit_logs.c.id.desc()
        ).limit(min(max(limit, 1), 500))
        if principal.data_scope != "all":
            statement = statement.where(
                request_audit_logs.c.organization_id
                == principal.organization_id
            )
        with self._engine.connect() as connection:
            return [
                dict(row)
                for row in connection.execute(statement).mappings()
            ]

    def enqueue_task(
        self,
        principal: Principal,
        *,
        kind: str,
        payload: dict[str, Any],
        idempotency_key: str,
        max_attempts: int = 3,
    ) -> tuple[TaskRecord, bool]:
        now = int(time.time())
        with self._engine.begin() as connection:
            existing = connection.execute(
                select(background_tasks).where(
                    background_tasks.c.organization_id
                    == principal.organization_id,
                    background_tasks.c.kind == kind,
                    background_tasks.c.idempotency_key
                    == idempotency_key,
                )
            ).mappings().one_or_none()
            if existing is not None:
                return _task_record(existing), False
            task_id = _primary_key(
                connection.execute(
                    insert(background_tasks).values(
                        organization_id=principal.organization_id,
                        user_id=principal.user_id,
                        kind=kind,
                        status="queued",
                        payload_json=json.dumps(
                            payload,
                            ensure_ascii=False,
                        ),
                        progress=0,
                        attempts=0,
                        max_attempts=max_attempts,
                        idempotency_key=idempotency_key,
                        created_at=now,
                    )
                )
            )
            row = connection.execute(
                select(background_tasks).where(
                    background_tasks.c.id == task_id
                )
            ).mappings().one()
            return _task_record(row), True

    def get_task(
        self,
        principal: Principal,
        task_id: int,
    ) -> TaskRecord | None:
        statement = select(background_tasks).where(
            background_tasks.c.id == task_id
        )
        if principal.data_scope != "all":
            statement = statement.where(
                background_tasks.c.organization_id
                == principal.organization_id
            )
        with self._engine.connect() as connection:
            row = connection.execute(statement).mappings().one_or_none()
        return None if row is None else _task_record(row)

    def list_tasks(
        self,
        principal: Principal,
        *,
        limit: int = 100,
    ) -> list[TaskRecord]:
        statement = select(background_tasks).order_by(
            background_tasks.c.id.desc()
        ).limit(min(max(limit, 1), 500))
        if principal.data_scope != "all":
            statement = statement.where(
                background_tasks.c.organization_id
                == principal.organization_id
            )
        with self._engine.connect() as connection:
            return [
                _task_record(row)
                for row in connection.execute(statement).mappings()
            ]

    def claim_task(self, task_id: int) -> TaskRecord | None:
        now = int(time.time())
        with self._engine.begin() as connection:
            result = connection.execute(
                update(background_tasks)
                .where(
                    background_tasks.c.id == task_id,
                    background_tasks.c.status.in_(
                        ("queued", "retry")
                    ),
                )
                .values(
                    status="running",
                    progress=1,
                    attempts=background_tasks.c.attempts + 1,
                    started_at=now,
                    error=None,
                )
            )
            if not result.rowcount:
                return None
            row = connection.execute(
                select(background_tasks).where(
                    background_tasks.c.id == task_id
                )
            ).mappings().one()
            return _task_record(row)

    def update_task_progress(
        self,
        task_id: int,
        progress: int,
    ) -> None:
        with self._engine.begin() as connection:
            connection.execute(
                update(background_tasks)
                .where(background_tasks.c.id == task_id)
                .values(progress=min(max(progress, 1), 99))
            )

    def complete_task(
        self,
        task_id: int,
        result: dict[str, Any],
    ) -> None:
        with self._engine.begin() as connection:
            connection.execute(
                update(background_tasks)
                .where(background_tasks.c.id == task_id)
                .values(
                    status="completed",
                    result_json=json.dumps(
                        result,
                        ensure_ascii=False,
                    ),
                    progress=100,
                    finished_at=int(time.time()),
                )
            )

    def fail_task(self, task_id: int, error: str) -> bool:
        with self._engine.begin() as connection:
            row = connection.execute(
                select(
                    background_tasks.c.attempts,
                    background_tasks.c.max_attempts,
                ).where(background_tasks.c.id == task_id)
            ).mappings().one()
            retry = int(row["attempts"]) < int(row["max_attempts"])
            connection.execute(
                update(background_tasks)
                .where(background_tasks.c.id == task_id)
                .values(
                    status="retry" if retry else "failed",
                    error=error[:4000],
                    finished_at=None if retry else int(time.time()),
                )
            )
            return retry

    def retry_task(
        self,
        principal: Principal,
        task_id: int,
    ) -> TaskRecord | None:
        task = self.get_task(principal, task_id)
        if task is None or task.status != "failed":
            return None
        with self._engine.begin() as connection:
            connection.execute(
                update(background_tasks)
                .where(background_tasks.c.id == task_id)
                .values(
                    status="retry",
                    attempts=0,
                    progress=0,
                    error=None,
                    finished_at=None,
                )
            )
        return self.get_task(principal, task_id)

    def recoverable_task_ids(self) -> list[int]:
        with self._engine.begin() as connection:
            connection.execute(
                update(background_tasks)
                .where(background_tasks.c.status == "running")
                .values(status="retry")
            )
            return [
                int(value)
                for value in connection.execute(
                    select(background_tasks.c.id).where(
                        background_tasks.c.status.in_(
                            ("queued", "retry")
                        )
                    )
                ).scalars()
            ]

    def task_principal(self, task: TaskRecord) -> Principal:
        with self._engine.connect() as connection:
            return self._principal_for_user(
                connection,
                task.user_id,
                session_id=None,
                auth_method="task",
            )

    def _principal_for_user(
        self,
        connection,
        user_id: int,
        *,
        session_id: int | None,
        auth_method: str,
    ) -> Principal:
        user = connection.execute(
            select(
                users.c.id,
                users.c.organization_id,
                users.c.username,
            ).where(users.c.id == user_id, users.c.active == 1)
        ).mappings().one()
        role_rows = connection.execute(
            select(
                roles.c.code,
                roles.c.data_scope,
                role_permissions.c.permission,
            )
            .select_from(
                user_roles.join(
                    roles,
                    roles.c.id == user_roles.c.role_id,
                ).join(
                    role_permissions,
                    role_permissions.c.role_id == roles.c.id,
                )
            )
            .where(user_roles.c.user_id == user_id)
        ).mappings()
        permissions: set[str] = set()
        role_codes: set[str] = set()
        data_scope = "organization"
        for row in role_rows:
            role_codes.add(str(row["code"]))
            permissions.add(str(row["permission"]))
            if row["data_scope"] == "all":
                data_scope = "all"
        return Principal(
            user_id=int(user["id"]),
            organization_id=int(user["organization_id"]),
            username=str(user["username"]),
            permissions=frozenset(permissions),
            data_scope=data_scope,
            session_id=session_id,
            auth_method=auth_method,
            role_codes=frozenset(role_codes),
        )


@lru_cache(maxsize=32)
def identity_store_for(config: AppConfig) -> IdentityStore:
    return IdentityStore(config)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    iterations = 240_000
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        iterations,
    )
    return (
        f"pbkdf2_sha256${iterations}${salt.hex()}${digest.hex()}"
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, raw_iterations, raw_salt, raw_digest = encoded.split(
            "$",
            3,
        )
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            bytes.fromhex(raw_salt),
            int(raw_iterations),
        )
        return hmac.compare_digest(digest.hex(), raw_digest)
    except (TypeError, ValueError):
        return False


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _primary_key(result) -> int:
    primary_key = result.inserted_primary_key
    if not primary_key or primary_key[0] is None:
        raise RuntimeError("database did not return a primary key")
    return int(primary_key[0])


def _identity_engine(config: AppConfig) -> Engine:
    if config.runtime_mode is RuntimeMode.LOCAL:
        config.database_path.parent.mkdir(parents=True, exist_ok=True)
        url = URL.create(
            "sqlite+pysqlite",
            database=str(config.database_path),
        )
        return create_engine(url, poolclass=NullPool)
    if not config.database_url:
        raise RuntimeError("online database URL is required")
    database_url = config.database_url
    if database_url.startswith("postgresql://"):
        database_url = database_url.replace(
            "postgresql://",
            "postgresql+psycopg://",
            1,
        )
    return create_engine(
        database_url,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
    )


def task_payload(task: TaskRecord) -> dict[str, Any]:
    return {
        "id": task.id,
        "kind": task.kind,
        "status": task.status,
        "progress": task.progress,
        "attempts": task.attempts,
        "max_attempts": task.max_attempts,
        "result": task.result,
        "error": task.error or "",
        "created_for_organization": task.organization_id,
    }


def _task_record(row) -> TaskRecord:
    return TaskRecord(
        id=int(row["id"]),
        organization_id=int(row["organization_id"]),
        user_id=int(row["user_id"]),
        kind=str(row["kind"]),
        status=str(row["status"]),
        payload=json.loads(row["payload_json"]),
        result=(
            None
            if not row["result_json"]
            else json.loads(row["result_json"])
        ),
        error=None if row["error"] is None else str(row["error"]),
        progress=int(row["progress"]),
        attempts=int(row["attempts"]),
        max_attempts=int(row["max_attempts"]),
        idempotency_key=str(row["idempotency_key"]),
    )
