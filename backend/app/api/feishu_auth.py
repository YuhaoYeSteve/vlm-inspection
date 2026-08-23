from typing import Annotated

from fastapi import APIRouter, Depends

from app.dependencies import get_feishu_connection_provider
from app.schemas import FeishuAuthStatus
from app.services.lark_cli import FeishuConnectionProviderProtocol

router = APIRouter(prefix="/api/auth/feishu", tags=["feishu-auth"])


@router.get("/status", response_model=FeishuAuthStatus)
async def authorization_status(
    provider: Annotated[
        FeishuConnectionProviderProtocol,
        Depends(get_feishu_connection_provider),
    ],
) -> FeishuAuthStatus:
    return FeishuAuthStatus.model_validate(await provider.status(), from_attributes=True)
