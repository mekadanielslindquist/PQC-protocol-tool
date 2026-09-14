from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from management_api import crypto_bridge
from management_api.config import ORGANIZATIONS

router = APIRouter(prefix="/keys", tags=["keys"])


class GenerateKeysRequest(BaseModel):
    org_id: str


@router.get("/organizations")
def organizations():
    return {"organizations": ORGANIZATIONS}


@router.get("/{org_id}")
def key_status(org_id: str):
    """Which key files exist for an org, and a fingerprint of each public
    key. Never returns private key bytes.
    """
    return crypto_bridge.key_status_for_org(org_id)


@router.post("/generate")
def generate_keys(request: GenerateKeysRequest):
    """Generate a fresh Falcon-1024 + Kyber-512 keypair for an organization.

    Returns metadata only (paths + public key fingerprints). Requires the
    native Falcon/Kyber libraries to be compiled for the local platform -
    see GET /crypto/status if this fails.
    """
    try:
        return crypto_bridge.generate_keys_for_org(request.org_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except crypto_bridge.CryptoUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                "Falcon/Kyber crypto backend is not available on this "
                f"machine: {exc}. See sip_connect/compile_falcon_library.sh "
                "(it targets aarch64 by default) and sip_connect/kyber/ref/ "
                "for building the native libraries, or check GET /crypto/status."
            ),
        )
    except Exception as exc:  # noqa: BLE001 - surface it, don't crash the API
        raise HTTPException(status_code=500, detail=f"Key generation failed: {exc}")
