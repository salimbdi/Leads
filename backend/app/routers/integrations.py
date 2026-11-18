import hmac
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from backend.app.config import get_settings
from backend.app.db import get_db
from backend.app.schemas.n8n import N8nCallbackRequest, N8nCallbackResponse
from backend.app.services.n8n_service import process_n8n_callback

router = APIRouter(prefix="/api/v1/integrations/n8n", tags=["integrations"])


def verify_callback_secret(
    x_callback_secret: Optional[str] = Header(default=None, alias="X-Callback-Secret"),
) -> None:
    """Authenticate inbound n8n callback requests using constant-time secret comparison."""
    settings = get_settings()
    expected_secret = settings.n8n_callback_secret.strip()
    if not expected_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Callback authentication secret is not configured on the server.",
        )
    if x_callback_secret is None or not x_callback_secret.strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-Callback-Secret header.",
        )
    provided_secret = x_callback_secret.strip()
    if not hmac.compare_digest(
        provided_secret.encode("utf-8"),
        expected_secret.encode("utf-8"),
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid X-Callback-Secret header.",
        )


@router.post(
    "/callback",
    response_model=N8nCallbackResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(verify_callback_secret)],
    summary="Receive batch business discovery and AI qualification results from n8n",
)
def n8n_batch_callback_endpoint(
    payload: N8nCallbackRequest,
    db: Session = Depends(get_db),
) -> N8nCallbackResponse:
    """Process an authenticated n8n batch callback and persist discovered leads idempotently."""
    return process_n8n_callback(db, payload)
