"""알림 수신 거부 DTO (계획서 7.2, 7.4)."""

from pydantic import BaseModel, ConfigDict, Field


class UnsubscribeRequest(BaseModel):
    """알림 메일의 수신 거부 링크에 들어 있던 토큰."""

    model_config = ConfigDict(extra="forbid")

    token: str = Field(max_length=1024)
