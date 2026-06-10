from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import decode_token
from app.db.session import get_db
from app.models.entities import Usuario

bearer = HTTPBearer()


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: Session = Depends(get_db),
) -> Usuario:
    email = decode_token(credentials.credentials)
    if not email:
        raise HTTPException(status_code=401, detail="Token inválido.")
    user = db.scalar(select(Usuario).where(Usuario.email == email, Usuario.ativo.is_(True)))
    if not user:
        raise HTTPException(status_code=401, detail="Usuário não encontrado.")
    return user


def get_empresa_id(user: Usuario = Depends(get_current_user)) -> int:
    return user.empresa_id
