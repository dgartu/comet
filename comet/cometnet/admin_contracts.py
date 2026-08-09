"""Canonical validated contracts for CometNet pool administration."""

from typing import Annotated, Literal
from urllib.parse import urlsplit

from fastapi import Path
from pydantic import BaseModel, ConfigDict, Field, field_validator

from comet.cometnet.crypto import NodeIdentity
from comet.cometnet.identifiers import POOL_ID_PATTERN
from comet.utils.text import has_ascii_control

INVITE_CODE_PATTERN = r"^[A-Za-z0-9_-]{22}$"
PoolIdPath = Annotated[
    str,
    Path(pattern=POOL_ID_PATTERN),
]
InviteCodePath = Annotated[
    str,
    Path(pattern=INVITE_CODE_PATTERN),
]
MemberKeyPath = Annotated[
    str,
    Path(pattern=r"^[0-9a-f]{176}$"),
]


class StrictRequest(BaseModel):
    model_config = ConfigDict(strict=True)


class CreatePoolRequest(StrictRequest):
    pool_id: str = Field(pattern=POOL_ID_PATTERN)
    display_name: str
    description: str = ""
    join_mode: Literal["invite"] = "invite"


class CreateInviteRequest(StrictRequest):
    expires_in: int | None = Field(default=None, gt=0)
    max_uses: int | None = Field(default=None, ge=1)


class JoinPoolRequest(StrictRequest):
    invite_code: str = Field(
        pattern=INVITE_CODE_PATTERN,
    )
    node_url: str | None = None

    @field_validator("node_url")
    @classmethod
    def validate_node_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if has_ascii_control(value) or any(character.isspace() for character in value):
            raise ValueError("node URL contains invalid characters")
        try:
            parsed = urlsplit(value)
            port = parsed.port
        except ValueError:
            raise ValueError("node URL is invalid") from None
        if (
            parsed.scheme not in {"ws", "wss"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
            or (port is not None and not 1 <= port <= 65_535)
        ):
            raise ValueError("node URL is invalid")
        return value


class AddMemberRequest(StrictRequest):
    member_key: str
    role: Literal["admin", "member"] = "member"

    @field_validator("member_key")
    @classmethod
    def validate_member_key(cls, value: str) -> str:
        if NodeIdentity.load_public_key(value) is None:
            raise ValueError("member key is invalid")
        return value


class UpdateMemberRoleRequest(StrictRequest):
    role: Literal["admin", "member"]


class StandaloneCreatePoolRequest(StrictRequest):
    pool_id: str
    display_name: str
    description: str = ""
    join_mode: str = "invite"


class StandaloneJoinPoolRequest(StrictRequest):
    invite_code: str
    node_url: str | None = None


class StandaloneCreateInviteRequest(StrictRequest):
    expires_in: int | None = None
    max_uses: int | None = None


class StandaloneAddMemberRequest(StrictRequest):
    member_key: str
    role: str = "member"


class StandaloneUpdateMemberRoleRequest(StrictRequest):
    role: str
