import hashlib

import aiohttp

from .stremthru import StremThru

_DEBRID_EXTENSIONS = {
    "realdebrid": "RD",
    "alldebrid": "AD",
    "premiumize": "PM",
    "torbox": "TB",
    "debridlink": "DL",
    "stremthru": "ST",
    "debrider": "DB",
    "easydebrid": "ED",
    "offcloud": "OC",
    "pikpak": "PP",
    "torrent": "TORRENT",
}


def get_debrid_extension(debrid_service: str):
    return _DEBRID_EXTENSIONS[debrid_service]


def build_addon_name(
    base_name: str,
    config: dict,
    additional_extensions: tuple[str, ...] = (),
) -> str:
    extensions = []
    debrid_entries = config.get("_debridEntries", [])
    enable_torrent = config.get("_enableTorrent", False)

    for entry in debrid_entries:
        ext = get_debrid_extension(entry["service"])
        if ext not in extensions:
            extensions.append(ext)

    if enable_torrent and debrid_entries:
        extensions.append("TORRENT")

    for extension in additional_extensions:
        if extension and extension not in extensions:
            extensions.append(extension)

    extension_str = "+".join(extensions)
    return f"{base_name}{(' | ' + extension_str) if extension_str else ''}"


def build_account_key_hash(debrid_api_key: str) -> str:
    return hashlib.sha256((debrid_api_key or "").encode("utf-8")).hexdigest()


def build_playback_media_id(
    media_only_id: str,
    media_type: str,
    season: int | None,
    episode: int | None,
) -> str:
    is_imdb = media_only_id.startswith("tt")
    if media_type == "movie":
        return media_only_id if is_imdb else f"kitsu:{media_only_id}"
    if not is_imdb:
        return (
            f"kitsu:{media_only_id}:{episode}"
            if episode is not None
            else f"kitsu:{media_only_id}"
        )
    if season is None:
        return media_only_id
    if episode is None:
        return f"{media_only_id}:{season}"
    return f"{media_only_id}:{season}:{episode}"


def get_debrid_credentials(config: dict, service_index: int | None = None):
    debrid_entries = config.get("_debridEntries", [])

    if debrid_entries:
        index = (
            service_index
            if service_index is not None and 0 <= service_index < len(debrid_entries)
            else 0
        )
        entry = debrid_entries[index]
        return entry["service"], entry["apiKey"]

    return config.get("debridService", "torrent"), config.get("debridApiKey", "")


def get_debrid(
    session: aiohttp.ClientSession,
    video_id: str,
    media_only_id: str,
    debrid_service: str,
    debrid_api_key: str,
    ip: str,
):
    if debrid_service != "torrent":
        return StremThru(
            session,
            video_id,
            media_only_id,
            f"{debrid_service}:{debrid_api_key}",
            ip,
        )


async def retrieve_debrid_availability(
    session: aiohttp.ClientSession,
    video_id: str,
    media_only_id: str,
    debrid_service: str,
    debrid_api_key: str,
    ip: str,
    info_hashes: list,
    seeders_map: dict,
    tracker_map: dict,
    sources_map: dict,
    target_air_date: str | None = None,
):
    return await get_debrid(
        session, video_id, media_only_id, debrid_service, debrid_api_key, ip
    ).get_availability(
        info_hashes,
        seeders_map,
        tracker_map,
        sources_map,
        target_air_date=target_air_date,
    )
