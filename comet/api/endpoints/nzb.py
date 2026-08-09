"""Owner-bound delivery of brokered NZB artifacts."""

import hashlib
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from comet.core.config_validation import config_check
from comet.core.models import database, settings
from comet.core.provider_governor import ProviderGovernor
from comet.discovery import build_discovery_adapters
from comet.discovery.capabilities import record_discovery_capability_failure
from comet.playback.manager import (
    NzbSourceError,
    artifact_selection_hint,
    broker_nzb_release,
    resolve_nzb_handoff_intent,
)
from comet.playback.providers.stremio_nntp import (
    handoff_selector,
    validate_handoff_manifest,
)
from comet.playback.tokens import CapabilityCodec
from comet.usenet.engine_client import EngineClient
from comet.usenet.nzb_broker import NzbBroker, NzbBrokerError
from comet.usenet.provider_exports import (
    NzbProviderExportError,
    NzbProviderExportRepository,
)
from comet.utils.http_client import http_client_manager
from comet.utils.network import get_client_ip

router = APIRouter()
export_router = APIRouter()
_NZB_HANDOFF_REQUESTS_PER_MINUTE = 120


def _codec() -> CapabilityCodec:
    if not settings.USENET_ENABLED or not settings.COMET_CAPABILITY_SECRET:
        raise HTTPException(status_code=503, detail="Usenet is unavailable")
    return CapabilityCodec(settings.COMET_CAPABILITY_SECRET)


def _broker() -> NzbBroker:
    descriptor = Path(settings.USENET_RUNTIME_DIR) / "engine.json"
    return NzbBroker(settings.USENET_ARTIFACT_DIR, database, EngineClient(descriptor))


def _exports() -> NzbProviderExportRepository:
    return NzbProviderExportRepository(database)


def _artifact_grant_from_token(
    token: str, partition: bytes, codec: CapabilityCodec, *, now: int | None = None
) -> str:
    if not token.startswith("na1."):
        raise ValueError("invalid artifact capability")
    value = codec.decode(token, partition=partition, now=now)
    if len(value) != 6 or not isinstance(value[5], bytes) or len(value[5]) != 16:
        raise ValueError("invalid artifact capability")
    return str(uuid.UUID(bytes=value[5]))


async def _enforce_nzb_handoff_rate(
    partition: bytes,
    operation: str,
    identifiers: tuple[str, ...],
) -> None:
    digest = hashlib.sha256(b"comet-nzb-handoff-rate-v1\0")
    digest.update(partition)
    for identifier in identifiers:
        digest.update(uuid.UUID(identifier).bytes)
    permit = await ProviderGovernor(database).acquire_window(
        digest.digest(),
        operation,
        limit=_NZB_HANDOFF_REQUESTS_PER_MINUTE,
        window_seconds=60,
    )
    if permit is None:
        raise HTTPException(
            status_code=429,
            detail="NZB handoff request limit exceeded",
            headers={"Retry-After": "60"},
        )


@router.api_route(
    "/{b64config}/nzb/v1/{capability}.nzb",
    methods=["GET", "HEAD"],
    tags=["Stremio"],
    summary="Brokered NZB artifact",
)
async def artifact(b64config: str, capability: str):
    config = config_check(b64config)
    if config is None:
        raise HTTPException(status_code=400, detail="Invalid configuration")
    codec = _codec()
    try:
        partition = codec.configuration_partition_for_config(config)
        grant_id = _artifact_grant_from_token(capability, partition, codec)
        await _enforce_nzb_handoff_rate(
            partition,
            "nzb_artifact",
            (grant_id,),
        )
        reader = await _broker().acquire_granted_artifact(
            grant_id, owner_configuration_partition=partition
        )
    except (NzbBrokerError, ValueError):
        raise HTTPException(
            status_code=404, detail="NZB artifact is unavailable"
        ) from None
    return _artifact_response(reader, f"{grant_id}.nzb")


@router.api_route(
    "/{b64config}/nzb/intent/v2/{capability}.nzb",
    methods=["GET", "HEAD"],
    tags=["Stremio"],
    summary="Lazy owner-bound NZB handoff",
)
async def artifact_intent(request: Request, b64config: str, capability: str):
    """Broker one signed transform without accepting an origin from the client."""
    if not capability.startswith("ni2."):
        raise HTTPException(status_code=404, detail="NZB handoff is unavailable")
    config = config_check(b64config)
    if config is None:
        raise HTTPException(status_code=400, detail="Invalid configuration")
    codec = _codec()
    partition = codec.configuration_partition_for_config(config)
    session = await http_client_manager.get_session()
    user_session = await http_client_manager.get_user_session()
    broker = _broker()
    try:
        resolution = await resolve_nzb_handoff_intent(
            capability,
            config,
            database,
            session,
            client_ip=get_client_ip(request),
        )
    except ValueError:
        raise HTTPException(
            status_code=404,
            detail="NZB handoff is unavailable",
        ) from None
    selector = handoff_selector(
        resolution.release.title,
        resolution.intent.selection_intent,
    )
    if selector is None:
        raise HTTPException(
            status_code=404,
            detail="NZB handoff is unavailable",
        )
    await _enforce_nzb_handoff_rate(
        partition,
        "nzb_intent",
        (
            resolution.intent.candidate_id,
            resolution.intent.provider_configuration_id,
            *resolution.intent.locator_ids,
        ),
    )
    try:
        transformed = await broker_nzb_release(
            resolution.release,
            broker,
            database,
            build_discovery_adapters(
                config,
                session,
                user_session=user_session,
                database=database,
                account_partition=partition,
            ),
            provider_configuration_id=resolution.intent.provider_configuration_id,
            provider_kind="stremio_nntp",
            owner_configuration_partition=partition,
        )
        locators = transformed.locators
        payload = locators[0]["payload"]
        artifact_sha256 = payload["artifact_sha256"]
        artifact = await broker.resolve_owned_artifact(
            artifact_sha256,
            owner_configuration_partition=partition,
        )
        selection_hint = artifact_selection_hint(payload)
        try:
            validate_handoff_manifest(
                artifact.manifest,
                selector,
                selection_hint,
            )
        except ValueError:
            raise HTTPException(
                status_code=404,
                detail="NZB handoff is unavailable",
            ) from None
        reader = await broker.acquire_owned_artifact(
            artifact_sha256,
            owner_configuration_partition=partition,
        )
    except NzbSourceError as exc:
        if exc.auth_failed or exc.retryable:
            await record_discovery_capability_failure(
                config,
                codec,
                database,
                exc.source_configuration_id,
                state=("auth_failed" if exc.auth_failed else "transiently_unreachable"),
                error_code=("credentials_rejected" if exc.auth_failed else exc.code),
                retry_after=(
                    None
                    if exc.auth_failed
                    else (exc.retry_after if exc.retry_after is not None else 30)
                ),
            )
        raise HTTPException(
            status_code=404,
            detail="NZB handoff is unavailable",
        ) from None
    except (
        TimeoutError,
        NzbBrokerError,
    ):
        raise HTTPException(
            status_code=404,
            detail="NZB handoff is unavailable",
        ) from None
    return _artifact_response(reader, f"{artifact_sha256}.nzb")


@export_router.api_route(
    "/nzb/export/v1/{capability}.nzb",
    methods=["GET", "HEAD"],
    tags=["Stremio"],
    summary="Provider-scoped NZB export",
)
async def provider_export(capability: str):
    if not capability.startswith("nx1."):
        raise HTTPException(status_code=404, detail="NZB export is unavailable")
    try:
        export = await _exports().resolve(capability.removeprefix("nx1."))
        reader = await _broker().acquire_granted_artifact(
            export.grant_id,
            owner_configuration_partition=export.owner_configuration_partition,
        )
    except (NzbProviderExportError, NzbBrokerError, ValueError):
        raise HTTPException(
            status_code=404, detail="NZB export is unavailable"
        ) from None
    return _artifact_response(reader, f"{export.artifact_sha256}.nzb")


def _artifact_response(reader, filename: str) -> FileResponse:
    response = FileResponse(
        reader.path,
        media_type="application/x-nzb",
        filename=filename,
        background=BackgroundTask(reader.close),
    )
    response.headers["Content-Length"] = str(reader.byte_size)
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response
