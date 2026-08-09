import base64
import math
from decimal import Decimal

from RTN import ParsedData

from comet.core.models import settings
from comet.core.sources import MAX_SIGNED_BIGINT
from comet.metadata.media_info import MediaInfo
from comet.utils.languages import LANGUAGE_EMOJIS

_SIZE_MULTIPLIERS = {
    "b": 1,
    "kb": 1024,
    "mb": 1024**2,
    "gb": 1024**3,
    "tb": 1024**4,
}


def normalize_info_hash(info_hash: str) -> str:
    if len(info_hash) == 32:
        try:
            info_hash = base64.b16encode(base64.b32decode(info_hash.upper())).decode(
                "utf-8"
            )
        except ValueError:
            pass

    if len(info_hash) == 80:
        try:
            decoded_bytes = bytes.fromhex(info_hash)
            decoded_str = decoded_bytes.decode("ascii")
            if len(decoded_str) == 40:
                int(decoded_str, 16)  # Validate it's hex
                info_hash = decoded_str
        except ValueError:
            pass

    return info_hash.lower()


def format_bytes(bytes_value):
    if bytes_value is None:
        return None
    if isinstance(bytes_value, bool) or not isinstance(
        bytes_value, (int, float, Decimal)
    ):
        return None
    bytes_value = float(bytes_value)
    if not math.isfinite(bytes_value) or bytes_value < 0:
        return None

    for unit in ("B", "KB", "MB", "GB", "TB"):
        if bytes_value < 1024.0:
            return f"{bytes_value:.1f} {unit}"
        bytes_value /= 1024.0
    return f"{bytes_value:.1f} PB"


def size_to_bytes(size_str: str):
    if type(size_str) is not str:
        return None
    parts = size_str.split()
    if len(parts) != 2:
        return None
    value, unit = parts

    try:
        value = float(value)
    except ValueError:
        return None
    unit = unit.lower()

    multiplier = _SIZE_MULTIPLIERS.get(unit)
    if multiplier is None or not math.isfinite(value) or value < 0:
        return None

    size_bytes = value * multiplier
    if not math.isfinite(size_bytes) or size_bytes > MAX_SIGNED_BIGINT:
        return None
    return int(size_bytes)


def get_language_emoji(language: str):
    return LANGUAGE_EMOJIS.get(language.lower(), language)


def format_video_info(data: ParsedData):
    video_parts = []

    if data.codec:
        video_parts.append(data.codec)
    video_parts.extend(data.hdr)
    if data.bit_depth:
        video_parts.append(data.bit_depth)

    return " • ".join(video_parts)


def format_audio_info(data: ParsedData):
    audio_parts = [*data.audio, *data.channels]

    return " • ".join(audio_parts)


def format_quality_info(data: ParsedData):
    quality_parts = []

    if data.quality:
        quality_parts.append(data.quality)
    if data.edition:
        quality_parts.append(data.edition)
    if data.proper:
        quality_parts.append("PROPER")
    if data.repack:
        quality_parts.append("REPACK")
    if data.upscaled:
        quality_parts.append("UPSCALED")
    if data.remastered:
        quality_parts.append("REMASTERED")
    if data.extended:
        quality_parts.append("EXTENDED")

    return " • ".join(quality_parts)


def format_group_info(data: ParsedData):
    return data.group or ""


_STYLE_EMOJI = {
    "title": "📄 {}",
    "video": "📹 {}",
    "audio": "🔊 {}",
    "quality": "⭐ {}",
    "group": "🏷️ {}",
    "seeders": "👤 {}",
    "size": "💾 {}",
    "tracker": "🔎 {}",
    "tracker_clean": "🔎 Comet|{}",
    "languages": None,
    "subtitles": "💬 {}",
}

_STYLE_PLAIN = {
    "title": "{}",
    "video": "{}",
    "audio": "{}",
    "quality": "{}",
    "group": "{}",
    "seeders": "Seeders: {}",
    "size": "Size: {}",
    "tracker": "Source: {}",
    "tracker_clean": "Source: Comet|{}",
    "languages": "Languages: {}",
    "subtitles": "Subtitles: {}",
}


def _get_formatted_components(
    data: ParsedData,
    ttitle: str,
    seeders: int,
    size: int,
    tracker: str,
    result_format: list,
    style: dict,
    media_info: MediaInfo | None = None,
):
    requested = set(result_format)
    has_all = "all" in requested
    components = {}

    if has_all or "title" in requested:
        components["title"] = style["title"].format(ttitle)

    if has_all or "video_info" in requested:
        info = format_video_info(data)
        if info:
            components["video"] = style["video"].format(info)

    if has_all or "audio_info" in requested:
        info = format_audio_info(data)
        if info:
            components["audio"] = style["audio"].format(info)

    if has_all or "quality_info" in requested:
        info = format_quality_info(data)
        if info:
            components["quality"] = style["quality"].format(info)

    if has_all or "release_group" in requested:
        info = format_group_info(data)
        if info:
            components["group"] = style["group"].format(info)

    if (has_all or "seeders" in requested) and seeders is not None:
        components["seeders"] = style["seeders"].format(seeders)

    if (has_all or "size" in requested) and size is not None:
        components["size"] = style["size"].format(format_bytes(size))

    if (has_all or "tracker" in requested) and tracker:
        if settings.COMET_CLEAN_TRACKER and tracker.startswith("Comet|"):
            components["tracker"] = style["tracker_clean"].format(
                tracker.rsplit("|", 1)[-1]
            )
        else:
            components["tracker"] = style["tracker"].format(tracker)

    if (has_all or "languages" in requested) and data.languages:
        lang_fmt = style["languages"]
        if lang_fmt is None:
            components["languages"] = "/".join(
                get_language_emoji(language) for language in data.languages
            )
        else:
            components["languages"] = lang_fmt.format("/".join(data.languages))

    if (
        (has_all or "subtitles" in requested)
        and media_info is not None
        and media_info.subtitle_languages
    ):
        subtitles = "/".join(
            (get_language_emoji(language) if style["languages"] is None else language)
            for language in media_info.subtitle_languages
        )
        components["subtitles"] = style["subtitles"].format(subtitles)

    return components


def get_formatted_components(
    data: ParsedData,
    ttitle: str,
    seeders: int,
    size: int,
    tracker: str,
    result_format: list,
    media_info: MediaInfo | None = None,
):
    return _get_formatted_components(
        data,
        ttitle,
        seeders,
        size,
        tracker,
        result_format,
        _STYLE_EMOJI,
        media_info,
    )


def get_formatted_components_plain(
    data: ParsedData,
    ttitle: str,
    seeders: int,
    size: int,
    tracker: str,
    result_format: list,
    media_info: MediaInfo | None = None,
):
    return _get_formatted_components(
        data,
        ttitle,
        seeders,
        size,
        tracker,
        result_format,
        _STYLE_PLAIN,
        media_info,
    )


def format_title(components: dict):
    lines = []

    if "title" in components:
        lines.append(components["title"])

    video_audio = [components[k] for k in ["video", "audio"] if k in components]
    if video_audio:
        lines.append(" | ".join(video_audio))

    quality_group = [components[k] for k in ["quality", "group"] if k in components]
    if quality_group:
        lines.append(" | ".join(quality_group))

    info = [components[k] for k in ["seeders", "size", "tracker"] if k in components]
    if info:
        lines.append(" ".join(info))

    if "languages" in components:
        lines.append(components["languages"])

    if "subtitles" in components:
        lines.append(components["subtitles"])

    if not lines:
        return "Empty result format configuration"

    return "\n".join(lines)


def format_chilllink(components: dict, cached: bool):
    metadata = ["⚡ Instant" if cached else "⬇️ Not Cached"]

    for key, value in components.items():
        if key != "title":
            metadata.append(value)

    return metadata
