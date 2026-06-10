from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import create_access_token, verify_password
from app.db.session import get_db
from app.models.entities import Usuario
from app.schemas.common import LoginPayload, Token

router = APIRouter(prefix="/auth", tags=["Autenticação"])


@router.post("/login", response_model=Token)
def login(payload: LoginPayload, db: Session = Depends(get_db)):
    user = db.scalar(select(Usuario).where(Usuario.email == payload.email, Usuario.ativo.is_(True)))
    if not user or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(401, "Credenciais inválidas.")
    return Token(access_token=create_access_token(user.email))
