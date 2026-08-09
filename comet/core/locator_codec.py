"""Canonical credential-free serialization for release locators."""

import orjson

from comet.core.sources import (
    EasynewsHttpRef,
    Locator,
    LocatorKind,
    LocatorPolicy,
    NzbArtifactRef,
    RealNzbRef,
    TorrentLocator,
)


def locator_json(locator: Locator) -> str:
    payload: dict[str, object]
    if isinstance(locator, TorrentLocator):
        payload = {"info_hash": locator.info_hash}
        selection_values = (
            locator.file_index,
            locator.selection_title,
            locator.selection_size,
            locator.selection_parsed_json,
        )
        has_selection = (
            any(value is not None for value in selection_values)
            or locator.season_norm != -1
            or locator.episode_norm != -1
        )
        if has_selection:
            payload.update(
                {
                    "file_index": locator.file_index,
                    "season_norm": locator.season_norm,
                    "episode_norm": locator.episode_norm,
                    "selection_title": locator.selection_title,
                    "selection_size": locator.selection_size,
                    "selection_parsed_json": locator.selection_parsed_json,
                }
            )
    elif isinstance(locator, RealNzbRef):
        payload = {
            "adapter_configuration_id": locator.adapter_configuration_id,
            "remote_guid": locator.remote_guid,
        }
    elif isinstance(locator, NzbArtifactRef):
        payload = {
            "artifact_sha256": locator.artifact_sha256,
            "manifest_identity": locator.manifest_identity,
        }
        if (
            locator.selection_hint_name is not None
            or locator.selection_hint_size is not None
        ):
            payload.update(
                {
                    "selection_hint_name": locator.selection_hint_name,
                    "selection_hint_size": locator.selection_hint_size,
                }
            )
    elif isinstance(locator, EasynewsHttpRef):
        payload = {
            "account_configuration_id": locator.account_configuration_id,
            "file_identifier": locator.file_identifier,
            "dlFarm": locator.download_farm,
            "dlPort": locator.download_port,
            "hash": locator.content_hash,
            "id": locator.item_identifier,
            "filename": locator.filename,
            "extension": locator.extension,
            "signature": locator.signature,
            "byte_size": locator.byte_size,
        }
    else:
        raise ValueError("unsupported locator type")
    return _canonical_json(
        payload,
        None if isinstance(locator, TorrentLocator) else 65_536,
        "locator",
    )


def policy_json(locator: Locator) -> str:
    policy = locator.policy
    owner_partition = policy.owner_configuration_partition
    return _canonical_json(
        {
            "allowed_provider_kinds": sorted(policy.allowed_provider_kinds),
            "exact_provider_configuration_id": (policy.exact_provider_configuration_id),
            "expires_at": policy.expires_at,
            "owner_configuration_partition": (
                owner_partition.hex() if owner_partition is not None else None
            ),
        },
        None if isinstance(locator, TorrentLocator) else 16_384,
        "locator policy",
    )


def parsed_json(parsed: object | None, *, trusted: bool = False) -> str:
    if parsed is None:
        return "{}"
    model_dump = getattr(parsed, "model_dump", None)
    if callable(model_dump):
        parsed = model_dump()
    elif not trusted:
        raise ValueError("invalid parsed release")
    return _canonical_json(parsed, None if trusted else 65_536, "parsed release")


def locator_from_json(
    locator_id: str,
    kind: str,
    locator_payload: str,
    policy_payload: str,
) -> Locator:
    try:
        locator_kind = LocatorKind(kind)
        payload = orjson.loads(locator_payload)
        raw_policy = orjson.loads(policy_payload)
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid persisted locator") from exc
    policy = _policy_from_mapping(raw_policy)
    common = {
        "locator_id": locator_id,
        "kind": locator_kind,
        "policy": policy,
    }
    if locator_kind is LocatorKind.TORRENT:
        locator = TorrentLocator(
            **common,
            info_hash=payload.get("info_hash"),
            file_index=payload.get("file_index"),
            season_norm=payload.get("season_norm", -1),
            episode_norm=payload.get("episode_norm", -1),
            selection_title=payload.get("selection_title"),
            selection_size=payload.get("selection_size"),
            selection_parsed_json=payload.get("selection_parsed_json"),
        )
    elif locator_kind is LocatorKind.REAL_NZB:
        locator = RealNzbRef(
            **common,
            adapter_configuration_id=payload["adapter_configuration_id"],
            remote_guid=payload["remote_guid"],
        )
    elif locator_kind is LocatorKind.NZB_ARTIFACT:
        locator = NzbArtifactRef(
            **common,
            artifact_sha256=payload["artifact_sha256"],
            manifest_identity=payload["manifest_identity"],
            selection_hint_name=payload.get("selection_hint_name"),
            selection_hint_size=payload.get("selection_hint_size"),
        )
    else:
        locator = EasynewsHttpRef(
            **common,
            account_configuration_id=payload["account_configuration_id"],
            file_identifier=payload["file_identifier"],
            download_farm=payload["dlFarm"],
            download_port=payload["dlPort"],
            content_hash=payload["hash"],
            item_identifier=payload["id"],
            filename=payload["filename"],
            extension=payload["extension"],
            signature=payload.get("signature"),
            byte_size=payload.get("byte_size"),
        )
    return locator


def _policy_from_mapping(payload: dict) -> LocatorPolicy:
    owner_partition = payload.get("owner_configuration_partition")
    return LocatorPolicy(
        frozenset(payload.get("allowed_provider_kinds", ())),
        bytes.fromhex(owner_partition) if owner_partition is not None else None,
        payload.get("exact_provider_configuration_id"),
        payload.get("expires_at"),
    )


def _canonical_json(value: object, maximum: int | None, field: str) -> str:
    try:
        payload = orjson.dumps(value, option=orjson.OPT_SORT_KEYS)
    except TypeError as exc:
        raise ValueError(f"invalid {field} JSON") from exc
    if maximum is not None and len(payload) > maximum:
        raise ValueError(f"{field} JSON is too large")
    return payload.decode()
