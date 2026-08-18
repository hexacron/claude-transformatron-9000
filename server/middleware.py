"""Shared transform middleware hooks for policy and audit integrations.

This follows the official SDK middleware example, but keeps the adapters inert by
default so local transform development stays frictionless. Replace ``PolicyChecker``
or ``AuditWriter`` with real clients when this server needs per-transform
authorization or an audit trail.
"""

from typing import Any

from maltego.middlewares.middlewares import TransformMiddleware
from maltego.model.context import MaltegoContext
from maltego.model.entity import MaltegoEntity
from maltego.model.exception import MaltegoException
from maltego.model.graph import MaltegoGraph
from maltego.model.transform import MaltegoTransform
from maltego.model.types import ExecutionState, MaltegoSettingTypes


class PolicyChecker:
    """Authorization adapter used by ``AuthorizationMiddleware``."""

    async def allowed(self, *, identity: Any | None, claims: dict[str, Any] | None) -> bool:
        """Return whether the authenticated caller may run the transform."""
        return True


class AuditWriter:
    """Audit sink adapter used by ``AuditMiddleware``."""

    async def record(
        self,
        *,
        identity: Any | None,
        transform_name: str,
        outcome: str,
    ) -> None:
        """Record one transform run outcome."""


class AuthorizationMiddleware(TransformMiddleware):
    """Run policy checks after SDK authentication has resolved the caller."""

    def __init__(self, policy: PolicyChecker) -> None:
        self.policy = policy

    async def before_transform(
        self,
        transform: MaltegoTransform,
        transform_input: MaltegoEntity | list[MaltegoEntity] | MaltegoGraph[Any],
        properties: dict[str, MaltegoSettingTypes],
        context: MaltegoContext,
        soft_limit: int,
        hard_limit: int,
    ) -> None:
        allowed = await self.policy.allowed(
            identity=context.identity,
            claims=context.auth_claims,
        )
        if not allowed:
            raise MaltegoException("Not authorised to run this transform")

    async def after_transform(
        self,
        transform: MaltegoTransform,
        transform_input: MaltegoEntity | list[MaltegoEntity] | MaltegoGraph[Any],
        output_entities: list[MaltegoEntity],
        context: MaltegoContext,
        state: ExecutionState,
        exceptions: list[Exception] | None = None,
    ) -> None:
        pass


class AuditMiddleware(TransformMiddleware):
    """Record completion for every transform run, including failed runs."""

    call_on_failure = True

    def __init__(self, audit: AuditWriter) -> None:
        self.audit = audit

    async def before_transform(
        self,
        transform: MaltegoTransform,
        transform_input: MaltegoEntity | list[MaltegoEntity] | MaltegoGraph[Any],
        properties: dict[str, MaltegoSettingTypes],
        context: MaltegoContext,
        soft_limit: int,
        hard_limit: int,
    ) -> None:
        pass

    async def after_transform(
        self,
        transform: MaltegoTransform,
        transform_input: MaltegoEntity | list[MaltegoEntity] | MaltegoGraph[Any],
        output_entities: list[MaltegoEntity],
        context: MaltegoContext,
        state: ExecutionState,
        exceptions: list[Exception] | None = None,
    ) -> None:
        context.log.inform(f"{transform.display_name} finished with state {state}")
        await self.audit.record(
            identity=context.identity,
            transform_name=transform.display_name,
            outcome=state.value,
        )
