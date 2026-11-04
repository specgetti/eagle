"""Admin configuration: platform settings, agents and default models, credentials."""

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import select

from eagle.credentials import (
    AGENT_CREDENTIAL_KINDS,
    CREDENTIAL_FIELDS,
    SUBSCRIPTION_KINDS,
    CredentialError,
    decrypt_json,
    encrypt_json,
    resolve,
    split_fields,
)
from eagle.deps import AdminUser, Box, DbSession, audit
from eagle.models import AgentConfig, AgentKind, InferenceCredential, utcnow
from eagle.providers import ProviderError
from eagle.schemas import (
    AgentConfigOut,
    AgentConfigUpdate,
    CredentialCreate,
    CredentialFieldOut,
    CredentialOut,
    CredentialTestOut,
    CredentialTestRequest,
    CredentialTypeOut,
    CredentialUpdate,
    ModelListOut,
)
from eagle.security import SecretBox, mask_secret
from eagle.services import PlatformSettings, get_platform_settings, save_platform_settings

router = APIRouter(tags=["admin"])


# Platform settings


@router.get("/settings", response_model=PlatformSettings)
async def get_settings(_: AdminUser, db: DbSession) -> PlatformSettings:
    return await get_platform_settings(db)


@router.put("/settings", response_model=PlatformSettings)
async def update_settings(
    body: PlatformSettings, admin: AdminUser, db: DbSession
) -> PlatformSettings:
    await save_platform_settings(db, body)
    audit(db, admin, "settings.updated", details=body.model_dump(mode="json"))
    await db.commit()
    return body


# Agents


def _agent_out(config: AgentConfig) -> AgentConfigOut:
    out = AgentConfigOut.model_validate(config)
    out.compatible_credential_kinds = sorted(AGENT_CREDENTIAL_KINDS[config.agent])
    return out


@router.get("/agents", response_model=list[AgentConfigOut])
async def list_agents(_: AdminUser, db: DbSession) -> list[AgentConfigOut]:
    configs = await db.scalars(select(AgentConfig).order_by(AgentConfig.agent))
    return [_agent_out(c) for c in configs]


@router.put("/agents/{agent}", response_model=AgentConfigOut)
async def update_agent(
    agent: AgentKind, body: AgentConfigUpdate, admin: AdminUser, db: DbSession
) -> AgentConfigOut:
    config = await db.get(AgentConfig, agent)
    if config is None:
        config = AgentConfig(agent=agent, enabled=True, allowed_models=[])
        db.add(config)

    changes = body.model_dump(exclude_unset=True)
    if "credential_id" in changes and changes["credential_id"] is not None:
        credential = await db.get(InferenceCredential, changes["credential_id"])
        if credential is None:
            raise HTTPException(422, "Credential not found")
        if credential.kind not in AGENT_CREDENTIAL_KINDS[agent]:
            raise HTTPException(422, f"A {credential.kind} credential cannot be used with {agent}")
    if "allowed_models" in changes:
        changes["allowed_models"] = list(dict.fromkeys(m.strip() for m in body.allowed_models))
    if "default_model" in changes:
        changes["default_model"] = (body.default_model or "").strip() or None

    for field, value in changes.items():
        setattr(config, field, value)

    allowed = config.allowed_models or []
    if config.default_model and allowed and config.default_model not in allowed:
        raise HTTPException(422, "The default model must be one of the allowed models")

    audit(db, admin, "agent.updated", target=agent, details=changes)
    await db.commit()
    return _agent_out(config)


# Credentials


@router.get("/credentials/types", response_model=list[CredentialTypeOut])
async def credential_types(_: AdminUser) -> list[CredentialTypeOut]:
    return [
        CredentialTypeOut(
            kind=kind,
            fields=[
                CredentialFieldOut(
                    name=f.name,
                    label=f.label,
                    secret=f.secret,
                    required=f.required,
                    choices=list(f.choices) if f.choices else None,
                    help=f.help,
                )
                for f in fields
            ],
            agents=sorted(a for a, kinds in AGENT_CREDENTIAL_KINDS.items() if kind in kinds),
            subscription=kind in SUBSCRIPTION_KINDS,
        )
        for kind, fields in CREDENTIAL_FIELDS.items()
    ]


async def _credential_out(db, box: SecretBox, credential: InferenceCredential) -> CredentialOut:
    secret = decrypt_json(box, credential.encrypted_secret)
    used_by = list(
        await db.scalars(
            select(AgentConfig.agent).where(AgentConfig.credential_id == credential.id)
        )
    )
    return CredentialOut(
        id=credential.id,
        name=credential.name,
        kind=credential.kind,
        public_config=credential.public_config or {},
        secret_fields={k: mask_secret(v) for k, v in secret.items()},
        used_by=sorted(used_by),
        created_at=credential.created_at,
        updated_at=credential.updated_at,
    )


async def _credential_or_404(db, credential_id: int) -> InferenceCredential:
    credential = await db.get(InferenceCredential, credential_id)
    if credential is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Credential not found")
    return credential


@router.get("/credentials", response_model=list[CredentialOut])
async def list_credentials(_: AdminUser, db: DbSession, box: Box) -> list[CredentialOut]:
    rows = await db.scalars(select(InferenceCredential).order_by(InferenceCredential.name))
    return [await _credential_out(db, box, c) for c in rows]


@router.post("/credentials", response_model=CredentialOut, status_code=201)
async def create_credential(
    body: CredentialCreate, admin: AdminUser, db: DbSession, box: Box
) -> CredentialOut:
    if await db.scalar(select(InferenceCredential.id).where(InferenceCredential.name == body.name)):
        raise HTTPException(status.HTTP_409_CONFLICT, "A credential with this name exists")
    try:
        secret, public = split_fields(body.kind, body.values)
    except CredentialError as exc:
        raise HTTPException(422, str(exc)) from exc
    credential = InferenceCredential(
        name=body.name,
        kind=body.kind,
        encrypted_secret=encrypt_json(box, secret),
        public_config=public,
    )
    db.add(credential)
    audit(db, admin, "credential.created", target=body.name, details={"kind": body.kind})
    await db.commit()
    return await _credential_out(db, box, credential)


@router.patch("/credentials/{credential_id}", response_model=CredentialOut)
async def update_credential(
    credential_id: int, body: CredentialUpdate, admin: AdminUser, db: DbSession, box: Box
) -> CredentialOut:
    credential = await _credential_or_404(db, credential_id)
    if body.name and body.name != credential.name:
        taken = await db.scalar(
            select(InferenceCredential.id).where(InferenceCredential.name == body.name)
        )
        if taken:
            raise HTTPException(status.HTTP_409_CONFLICT, "A credential with this name exists")
        credential.name = body.name
    if body.values:
        existing = decrypt_json(box, credential.encrypted_secret)
        merged = {**(credential.public_config or {}), **body.values}
        try:
            secret, public = split_fields(credential.kind, merged, existing_secret=existing)
        except CredentialError as exc:
            raise HTTPException(422, str(exc)) from exc
        credential.encrypted_secret = encrypt_json(box, secret)
        credential.public_config = public
    credential.updated_at = utcnow()
    audit(
        db,
        admin,
        "credential.updated",
        target=credential.name,
        details={"fields": sorted(body.values)},
    )
    await db.commit()
    return await _credential_out(db, box, credential)


@router.delete("/credentials/{credential_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_credential(credential_id: int, admin: AdminUser, db: DbSession) -> None:
    credential = await _credential_or_404(db, credential_id)
    used_by = list(
        await db.scalars(
            select(AgentConfig.agent).where(AgentConfig.credential_id == credential_id)
        )
    )
    if used_by:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Credential is used by: {', '.join(sorted(used_by))}. Reassign those agents first.",
        )
    await db.delete(credential)
    audit(db, admin, "credential.deleted", target=credential.name)
    await db.commit()


@router.post("/credentials/{credential_id}/test", response_model=CredentialTestOut)
async def test_credential(
    credential_id: int,
    body: CredentialTestRequest,
    request: Request,
    admin: AdminUser,
    db: DbSession,
    box: Box,
) -> CredentialTestOut:
    """Check a credential with a cheap request (and a one-token call if a model is given)."""
    credential = await _credential_or_404(db, credential_id)
    model = (body.model or "").strip() or None
    result = await request.app.state.providers.test(resolve(box, credential), model)
    audit(
        db,
        admin,
        "credential.tested",
        target=credential.name,
        details={"model": model, "ok": result.ok},
    )
    await db.commit()
    return CredentialTestOut(**vars(result))


@router.get("/credentials/{credential_id}/models", response_model=ModelListOut)
async def credential_models(
    credential_id: int, request: Request, _: AdminUser, db: DbSession, box: Box
) -> ModelListOut:
    """Models the credential's endpoint serves, for the admin model pickers."""
    credential = await _credential_or_404(db, credential_id)
    try:
        listed = await request.app.state.providers.list_models(resolve(box, credential))
    except ProviderError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    return ModelListOut(models=listed.models, source=listed.source, message=listed.message)
