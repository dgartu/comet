"""File-scoped media metadata learned from an inspected stream."""

from collections.abc import Mapping
from dataclasses import asdict, dataclass

import orjson
from RTN import ParsedData

from comet.utils.languages import LANGUAGE_EMOJIS
from comet.utils.parsing import ensure_multi_language

_LANGUAGES = {
    "ara": "ar",
    "ben": "bn",
    "bul": "bg",
    "ces": "cs",
    "chi": "zh",
    "cze": "cs",
    "dan": "da",
    "deu": "de",
    "dut": "nl",
    "ell": "el",
    "eng": "en",
    "est": "et",
    "fas": "fa",
    "fin": "fi",
    "fra": "fr",
    "fre": "fr",
    "ger": "de",
    "gre": "el",
    "guj": "gu",
    "heb": "he",
    "hin": "hi",
    "hrv": "hr",
    "hun": "hu",
    "ind": "id",
    "ita": "it",
    "jpn": "ja",
    "kan": "kn",
    "kor": "ko",
    "lav": "lv",
    "lit": "lt",
    "mal": "ml",
    "mar": "mr",
    "may": "ms",
    "msa": "ms",
    "nld": "nl",
    "nor": "no",
    "pan": "pa",
    "per": "fa",
    "pol": "pl",
    "por": "pt",
    "ron": "ro",
    "rum": "ro",
    "rus": "ru",
    "slk": "sk",
    "slo": "sk",
    "slv": "sl",
    "spa": "es",
    "srp": "sr",
    "swe": "sv",
    "tam": "ta",
    "tel": "te",
    "tha": "th",
    "tur": "tr",
    "ukr": "uk",
    "vie": "vi",
    "zho": "zh",
}
_LANGUAGE_NAMES = {
    "arabic": "ar",
    "chinese": "zh",
    "dutch": "nl",
    "english": "en",
    "french": "fr",
    "german": "de",
    "greek": "el",
    "hebrew": "he",
    "hindi": "hi",
    "italian": "it",
    "japanese": "ja",
    "korean": "ko",
    "persian": "fa",
    "polish": "pl",
    "portuguese": "pt",
    "russian": "ru",
    "spanish": "es",
    "turkish": "tr",
    "ukrainian": "uk",
}
_VIDEO_CODECS = {
    "av1": "av1",
    "avc": "avc",
    "avc1": "avc",
    "divx": "xvid",
    "dx50": "xvid",
    "h264": "avc",
    "h265": "hevc",
    "hevc": "hevc",
    "mpeg2video": "mpeg",
    "mpeg4": "mpeg",
    "vc-1": "vc1",
    "vc1": "vc1",
    "wvc1": "vc1",
    "x264": "avc",
    "x265": "hevc",
    "xvid": "xvid",
}
_RESOLUTION_HEIGHTS = (2160, 1440, 1080, 720, 576, 480, 360, 240, 144)
_RESOLUTION_WIDTHS = (3840, 2560, 1920, 1280, 1024, 854, 640, 426, 256)
_SCHEMA_VERSION = 1
_BACKFILL_FIELDS = (
    "quality",
    "languages",
    "audio",
    "channels",
    "codec",
    "hdr",
    "bit_depth",
    "group",
)


def _string(value) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _integer(value) -> int | None:
    return value if type(value) is int and value >= 0 else None


def _flag(value) -> bool:
    return value is True


def _language(code, title=None) -> str | None:
    code = _string(code)
    if code is None:
        return None
    normalized = code.casefold().replace("_", "-")
    base = normalized.split("-", 1)[0]
    language = (
        base
        if len(base) == 2 and base.isascii() and base.isalpha()
        else _LANGUAGES.get(base) or _LANGUAGE_NAMES.get(normalized)
    )
    if language == "es" and "latin" in (_string(title) or "").casefold():
        return "la"
    return language if language in LANGUAGE_EMOJIS and language != "multi" else None


def _channel(layout, count) -> str | None:
    layout = (_string(layout) or "").casefold()
    if layout:
        layout = layout.split("(", 1)[0].split(maxsplit=1)[0]
    if layout == "mono":
        return "mono"
    if layout == "stereo":
        return "2.0"
    if layout == "quad":
        return "4.0"
    if layout and layout[0].isdigit():
        return layout
    count = _integer(count)
    return {1: "mono", 2: "2.0", 6: "5.1", 7: "6.1", 8: "7.1"}.get(
        count, str(count) if count else None
    )


def _audio_codecs(codec, profile) -> tuple[str, ...]:
    codec = (_string(codec) or "").casefold()
    profile = (_string(profile) or "").casefold()
    codecs = []
    if codec in {"eac3", "ec-3"}:
        codecs.append("Dolby Digital Plus")
    elif codec in {"ac3", "ac-3"}:
        codecs.append("Dolby Digital")
    elif codec == "truehd":
        codecs.append("TrueHD")
    elif codec in {"dca", "dts"}:
        codecs.append(
            "DTS Lossless"
            if any(token in profile for token in ("master audio", "dts-hd ma", "xll"))
            else "DTS Lossy"
        )
    elif codec in {"aac", "faad"}:
        codecs.append("AAC")
    elif codec == "flac":
        codecs.append("FLAC")
    elif codec == "opus":
        codecs.append("OPUS")
    elif codec in {"mp3", "mp3float"}:
        codecs.append("MP3")
    elif codec:
        codecs.append(codec.upper())
    if "atmos" in profile or "joc" in profile:
        codecs.append("Atmos")
    return tuple(codecs)


def _ordered_unique(values) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


def _compact(value):
    if isinstance(value, dict):
        compacted = {key: _compact(item) for key, item in value.items()}
        return {
            key: item
            for key, item in compacted.items()
            if item not in (None, False, [], {})
        }
    if isinstance(value, (list, tuple)):
        return [_compact(item) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class VideoInfo:
    codec: str | None = None
    hdr: tuple[str, ...] = ()
    width: int | None = None
    height: int | None = None

    @property
    def resolution(self) -> str | None:
        candidates = []
        for value, levels in (
            (self.height, _RESOLUTION_HEIGHTS),
            (self.width, _RESOLUTION_WIDTHS),
        ):
            if value:
                index = min(range(len(levels)), key=lambda i: abs(levels[i] - value))
                candidates.append(_RESOLUTION_HEIGHTS[index])
        return f"{max(candidates)}p" if candidates else None


@dataclass(frozen=True, slots=True)
class AudioTrack:
    codec: str | None = None
    profile: str | None = None
    language: str | None = None
    title: str | None = None
    channels: str | None = None
    commentary: bool = False
    default: bool = False
    dub: bool = False
    hearing_impaired: bool = False
    original: bool = False
    visual_impaired: bool = False

    @property
    def codecs(self) -> tuple[str, ...]:
        return _audio_codecs(self.codec, self.profile)


@dataclass(frozen=True, slots=True)
class SubtitleTrack:
    codec: str | None = None
    language: str | None = None
    title: str | None = None
    default: bool = False
    forced: bool = False
    hearing_impaired: bool = False


@dataclass(frozen=True, slots=True)
class ContainerInfo:
    name: str | None = None
    duration_seconds: float | None = None
    size: int | None = None
    bitrate: int | None = None


@dataclass(frozen=True, slots=True)
class MediaInfo:
    video: VideoInfo | None = None
    audio: tuple[AudioTrack, ...] = ()
    subtitles: tuple[SubtitleTrack, ...] = ()
    container: ContainerInfo | None = None
    has_chapters: bool = False
    source: str | None = None
    version: int = 0

    @property
    def audio_languages(self) -> tuple[str, ...]:
        return _ordered_unique(track.language for track in self.audio)

    @property
    def subtitle_languages(self) -> tuple[str, ...]:
        return _ordered_unique(track.language for track in self.subtitles)

    @property
    def audio_codecs(self) -> tuple[str, ...]:
        return _ordered_unique(codec for track in self.audio for codec in track.codecs)

    @property
    def audio_channels(self) -> tuple[str, ...]:
        if self.source:
            return ()
        return _ordered_unique(track.channels for track in self.audio)

    @property
    def preference_key(self) -> tuple[int, int, int]:
        richness = len(self.audio) + len(self.subtitles) + self.has_chapters
        richness += sum(
            value is not None
            for value in (
                self.video.codec if self.video else None,
                self.video.width if self.video else None,
                self.video.height if self.video else None,
                self.container.duration_seconds if self.container else None,
                self.container.size if self.container else None,
                self.container.bitrate if self.container else None,
            )
        )
        richness += len(self.video.hdr) if self.video else 0
        return (2 if not self.source else 1, self.version, richness)

    def to_dict(self) -> dict:
        return _compact({"schema": _SCHEMA_VERSION, **asdict(self)})


def media_info_from_stremthru(value) -> MediaInfo | None:
    if not isinstance(value, Mapping):
        return None

    raw_video = value.get("video")
    video = None
    if isinstance(raw_video, Mapping):
        raw_hdr = raw_video.get("hdr")
        hdr = (
            _ordered_unique(
                normalized for item in raw_hdr if (normalized := _normalize_hdr(item))
            )
            if isinstance(raw_hdr, list)
            else ()
        )
        raw_codec = (_string(raw_video.get("codec")) or "").casefold()
        candidate = VideoInfo(
            codec=_VIDEO_CODECS.get(raw_codec, raw_codec or None),
            hdr=hdr,
            width=_integer(raw_video.get("w")),
            height=_integer(raw_video.get("h")),
        )
        if any((candidate.codec, candidate.hdr, candidate.width, candidate.height)):
            video = candidate

    raw_audio = value.get("audio")
    audio = (
        tuple(
            AudioTrack(
                codec=_string(track.get("codec")),
                profile=_string(track.get("profile")),
                language=_language(track.get("lang"), track.get("title")),
                title=_string(track.get("title")),
                channels=_channel(track.get("ch_layout"), track.get("ch")),
                commentary=_flag(track.get("commentary")),
                default=_flag(track.get("default")),
                dub=_flag(track.get("dub")),
                hearing_impaired=_flag(track.get("hearing_impaired")),
                original=_flag(track.get("original")),
                visual_impaired=_flag(track.get("visual_impaired")),
            )
            for track in raw_audio
            if isinstance(track, Mapping)
        )
        if isinstance(raw_audio, (list, tuple))
        else ()
    )

    raw_subtitles = value.get("subtitle")
    subtitles = (
        tuple(
            SubtitleTrack(
                codec=_string(track.get("codec")),
                language=_language(track.get("lang"), track.get("title")),
                title=_string(track.get("title")),
                default=_flag(track.get("default")),
                forced=_flag(track.get("forced")),
                hearing_impaired=_flag(track.get("hearing_impaired")),
            )
            for track in raw_subtitles
            if isinstance(track, Mapping)
        )
        if isinstance(raw_subtitles, (list, tuple))
        else ()
    )

    raw_container = value.get("format")
    container = None
    if isinstance(raw_container, Mapping):
        duration = raw_container.get("dur")
        duration_seconds = (
            duration / 1_000_000_000
            if type(duration) in (int, float) and duration > 0
            else None
        )
        candidate = ContainerInfo(
            name=_string(raw_container.get("n")),
            duration_seconds=duration_seconds,
            size=_integer(raw_container.get("s")),
            bitrate=_integer(raw_container.get("br")),
        )
        if any(
            (
                candidate.name,
                candidate.duration_seconds,
                candidate.size,
                candidate.bitrate,
            )
        ):
            container = candidate

    media_info = MediaInfo(
        video=video,
        audio=audio,
        subtitles=subtitles,
        container=container,
        has_chapters=_flag(value.get("has_chapters")),
        source=_string(value.get("src")),
        version=_integer(value.get("v")) or 0,
    )
    return (
        media_info
        if any((video, audio, subtitles, container, media_info.has_chapters))
        else None
    )


def _normalize_hdr(value) -> str | None:
    value = (_string(value) or "").casefold()
    if value in {"dv", "dolby vision"}:
        return "DV"
    if value == "hdr10+":
        return "HDR10+"
    if value == "hdr10":
        return "HDR10"
    if value == "hlg":
        return "HLG"
    if value == "hdr":
        return "HDR"
    return None


def media_info_to_json(media_info: MediaInfo | None) -> str | None:
    return (
        orjson.dumps(media_info.to_dict()).decode() if media_info is not None else None
    )


def media_info_from_json(value) -> MediaInfo | None:
    if value is None:
        return None
    try:
        payload = orjson.loads(value)
        if not isinstance(payload, Mapping) or payload.get("schema") != _SCHEMA_VERSION:
            return None
        video_data = payload.get("video")
        video = (
            VideoInfo(
                codec=_string(video_data.get("codec")),
                hdr=_ordered_unique(video_data.get("hdr", ())),
                width=_integer(video_data.get("width")),
                height=_integer(video_data.get("height")),
            )
            if isinstance(video_data, Mapping)
            else None
        )
        audio = tuple(
            AudioTrack(**track)
            for track in payload.get("audio", ())
            if isinstance(track, dict)
        )
        subtitles = tuple(
            SubtitleTrack(**track)
            for track in payload.get("subtitles", ())
            if isinstance(track, dict)
        )
        container_data = payload.get("container")
        container = (
            ContainerInfo(**container_data)
            if isinstance(container_data, dict)
            else None
        )
        return MediaInfo(
            video=video,
            audio=audio,
            subtitles=subtitles,
            container=container,
            has_chapters=payload.get("has_chapters") is True,
            source=_string(payload.get("source")),
            version=_integer(payload.get("version")) or 0,
        )
    except (TypeError, ValueError):
        return None


def prefer_media_info(
    current: MediaInfo | None, candidate: MediaInfo | None
) -> MediaInfo | None:
    if candidate is None:
        return current
    if current is None or candidate.preference_key > current.preference_key:
        return candidate
    return current


def enrich_parsed(
    original: ParsedData | None,
    selected: ParsedData | None,
    media_info: MediaInfo | None,
) -> ParsedData | None:
    if selected is None and original is None:
        return None
    merged = (selected or original).model_copy(deep=True)
    if selected is not None and original is not None:
        if merged.resolution in (None, "unknown") and original.resolution not in (
            None,
            "unknown",
        ):
            merged.resolution = original.resolution
        for field in _BACKFILL_FIELDS:
            if not getattr(merged, field) and getattr(original, field):
                setattr(merged, field, getattr(original, field))

    if media_info is not None:
        if media_info.video is not None:
            if media_info.video.codec:
                merged.codec = media_info.video.codec
            if media_info.video.hdr:
                merged.hdr = list(media_info.video.hdr)
            if resolution := media_info.video.resolution:
                merged.resolution = resolution
        if languages := media_info.audio_languages:
            merged.languages = list(languages)
        if codecs := media_info.audio_codecs:
            merged.audio = list(codecs)
        if channels := media_info.audio_channels:
            merged.channels = list(channels)
        if media_info.container is not None and media_info.container.bitrate:
            merged.bitrate = f"{media_info.container.bitrate / 1_000_000:g}mbps"

    ensure_multi_language(merged)
    return merged
