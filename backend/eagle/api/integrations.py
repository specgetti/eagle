"""Repo settings, git hosts (GitHub tokens) and notification channels."""

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import select

from eagle.credentials import decrypt_json, encrypt_json
from eagle.deps import AdminUser, AppSettings, Box, DbSession, audit
from eagle.github import default_api_url
from eagle.models import GitHost, NotificationChannel, RepoSetting
from eagle.notify import ChannelConfigError, NotificationError, ReviewEvent, split_channel_config
from eagle.schemas import (
    ChannelCreate,
    ChannelOut,
    ChannelUpdate,
    GitHostIn,
    GitHostOut,
    RepoSettingIn,
    RepoSettingOut,
)
from eagle.security import SecretBox, mask_secret

router = APIRouter(tags=["admin"])


# Repo settings


@router.get("/repo-settings", response_model=list[RepoSettingOut])
async def list_repo_settings(_: AdminUser, db: DbSession) -> list[RepoSetting]:
    return list(await db.scalars(select(RepoSetting).order_by(RepoSetting.pattern)))


@router.post("/repo-settings", response_model=RepoSettingOut, status_code=201)
async def create_repo_setting(body: RepoSettingIn, admin: AdminUser, db: DbSession) -> RepoSetting:
    if await db.scalar(select(RepoSetting.id).where(RepoSetting.pattern == body.pattern)):
        raise HTTPException(status.HTTP_409_CONFLICT, "A setting for this pattern exists")
    row = RepoSetting(**body.model_dump())
    db.add(row)
    audit(db, admin, "repo_setting.created", target=body.pattern)
    await db.commit()
    return row


@router.put("/repo-settings/{setting_id}", response_model=RepoSettingOut)
async def update_repo_setting(
    setting_id: int, body: RepoSettingIn, admin: AdminUser, db: DbSession
) -> RepoSetting:
    row = await db.get(RepoSetting, setting_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Repo setting not found")
    clash = await db.scalar(
        select(RepoSetting.id).where(RepoSetting.pattern == body.pattern, RepoSetting.id != row.id)
    )
    if clash:
        raise HTTPException(status.HTTP_409_CONFLICT, "A setting for this pattern exists")
    for field, value in body.model_dump().items():
        setattr(row, field, value)
    audit(db, admin, "repo_setting.updated", target=body.pattern)
    await db.commit()
    return row


@router.delete("/repo-settings/{setting_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_repo_setting(setting_id: int, admin: AdminUser, db: DbSession) -> None:
    row = await db.get(RepoSetting, setting_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Repo setting not found")
    await db.delete(row)
    audit(db, admin, "repo_setting.deleted", target=row.pattern)
    await db.commit()


# Git hosts


def _host_out(row: GitHost) -> GitHostOut:
    return GitHostOut(
        id=row.id,
        host=row.host,
        api_url=row.api_url,
        has_token=bool(row.encrypted_token),
        created_at=row.created_at,
    )


def _check_api_url(url: str) -> str:
    if not url.startswith("https://"):
        raise HTTPException(422, "api_url must be an https URL")
    return url.rstrip("/")


@router.get("/git-hosts", response_model=list[GitHostOut])
async def list_git_hosts(_: AdminUser, db: DbSession) -> list[GitHostOut]:
    return [_host_out(r) for r in await db.scalars(select(GitHost).order_by(GitHost.host))]


@router.post("/git-hosts", response_model=GitHostOut, status_code=201)
async def create_git_host(body: GitHostIn, admin: AdminUser, db: DbSession, box: Box) -> GitHostOut:
    host = body.host.lower()
    if await db.scalar(select(GitHost.id).where(GitHost.host == host)):
        raise HTTPException(status.HTTP_409_CONFLICT, "This host is already configured")
    row = GitHost(
        host=host,
        api_url=_check_api_url(body.api_url or default_api_url(host)),
        encrypted_token=box.encrypt(body.token) if body.token else None,
    )
    db.add(row)
    audit(db, admin, "git_host.created", target=host)
    await db.commit()
    return _host_out(row)


@router.put("/git-hosts/{host_id}", response_model=GitHostOut)
async def update_git_host(
    host_id: int, body: GitHostIn, admin: AdminUser, db: DbSession, box: Box
) -> GitHostOut:
    row = await db.get(GitHost, host_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Git host not found")
    row.host = body.host.lower()
    row.api_url = _check_api_url(body.api_url or default_api_url(row.host))
    if body.token is not None:
        row.encrypted_token = box.encrypt(body.token) if body.token else None
    audit(db, admin, "git_host.updated", target=row.host)
    await db.commit()
    return _host_out(row)


@router.delete("/git-hosts/{host_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_git_host(host_id: int, admin: AdminUser, db: DbSession) -> None:
    row = await db.get(GitHost, host_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Git host not found")
    await db.delete(row)
    audit(db, admin, "git_host.deleted", target=row.host)
    await db.commit()


# Notification channels


def _channel_out(box: SecretBox, row: NotificationChannel) -> ChannelOut:
    secret = decrypt_json(box, row.encrypted_secret)
    return ChannelOut(
        id=row.id,
        name=row.name,
        kind=row.kind,
        enabled=row.enabled,
        events=row.events or [],
        public_config=row.public_config or {},
        secret_fields={k: mask_secret(v) for k, v in secret.items()},
        created_at=row.created_at,
    )


async def _channel_or_404(db, channel_id: int) -> NotificationChannel:
    row = await db.get(NotificationChannel, channel_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Channel not found")
    return row


@router.get("/notifications", response_model=list[ChannelOut])
async def list_channels(_: AdminUser, db: DbSession, box: Box) -> list[ChannelOut]:
    rows = await db.scalars(select(NotificationChannel).order_by(NotificationChannel.name))
    return [_channel_out(box, r) for r in rows]


@router.post("/notifications", response_model=ChannelOut, status_code=201)
async def create_channel(
    body: ChannelCreate, admin: AdminUser, db: DbSession, box: Box
) -> ChannelOut:
    if await db.scalar(select(NotificationChannel.id).where(NotificationChannel.name == body.name)):
        raise HTTPException(status.HTTP_409_CONFLICT, "A channel with this name exists")
    try:
        secret, public = split_channel_config(body.kind, body.config)
    except (ChannelConfigError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc
    row = NotificationChannel(
        name=body.name,
        kind=body.kind,
        enabled=body.enabled,
        events=list(body.events),
        encrypted_secret=encrypt_json(box, secret),
        public_config=public,
    )
    db.add(row)
    audit(db, admin, "notification.created", target=body.name, details={"kind": body.kind})
    await db.commit()
    return _channel_out(box, row)


@router.patch("/notifications/{channel_id}", response_model=ChannelOut)
async def update_channel(
    channel_id: int, body: ChannelUpdate, admin: AdminUser, db: DbSession, box: Box
) -> ChannelOut:
    row = await _channel_or_404(db, channel_id)
    if body.name is not None:
        row.name = body.name
    if body.enabled is not None:
        row.enabled = body.enabled
    if body.events is not None:
        row.events = list(body.events)
    if body.config is not None:
        existing = decrypt_json(box, row.encrypted_secret)
        try:
            secret, public = split_channel_config(row.kind, body.config, existing)
        except (ChannelConfigError, ValueError) as exc:
            raise HTTPException(422, str(exc)) from exc
        row.encrypted_secret = encrypt_json(box, secret)
        row.public_config = public
    audit(db, admin, "notification.updated", target=row.name)
    await db.commit()
    return _channel_out(box, row)


@router.delete("/notifications/{channel_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_channel(channel_id: int, admin: AdminUser, db: DbSession) -> None:
    row = await _channel_or_404(db, channel_id)
    await db.delete(row)
    audit(db, admin, "notification.deleted", target=row.name)
    await db.commit()


@router.post("/notifications/{channel_id}/test", status_code=status.HTTP_204_NO_CONTENT)
async def test_channel(
    channel_id: int, request: Request, admin: AdminUser, db: DbSession, settings: AppSettings
) -> None:
    row = await _channel_or_404(db, channel_id)
    event = ReviewEvent(
        event="review.succeeded",
        review_id=0,
        repo="example/repo",
        pr_number=1,
        pr_url="https://github.com/example/repo/pull/1",
        title="Test notification from Eagle",
        agent="claude",
        model=None,
        verdict="comment",
        summary="This is a test message. If you can read it, the channel works.",
        error=None,
        review_url=settings.public_url,
        submitter_email=admin.email,
    )
    try:
        await request.app.state.notifier.send(row, event)
    except NotificationError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
