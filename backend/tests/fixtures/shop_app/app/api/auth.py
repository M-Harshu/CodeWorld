from fastapi import APIRouter, Depends
from app.services.auth_service import AuthService
from app.api import deps

router = APIRouter()


@router.post("/login")
def login(email: str, password: str, svc: AuthService = Depends(deps.get_auth_service)):
    """Log a user in and return a token."""
    token = svc.login(email, password)
    return {"token": token}


@router.post("/register")
def register(email: str, password: str, svc: AuthService = Depends(deps.get_auth_service)):
    user = svc.register(email, password)
    return {"id": user.id}
