from typing import Any, Type

from sqlalchemy import select
from sqlalchemy.orm import Session


def list_entities(db: Session, model: Type, empresa_id: int, filters: dict[str, Any] | None = None):
    stmt = select(model).where(model.empresa_id == empresa_id)
    for key, value in (filters or {}).items():
        if value is not None and hasattr(model, key):
            stmt = stmt.where(getattr(model, key) == value)
    return db.scalars(stmt).all()


def get_entity(db: Session, model: Type, entity_id: int, empresa_id: int):
    return db.scalar(select(model).where(model.id == entity_id, model.empresa_id == empresa_id))


def create_entity(db: Session, model: Type, empresa_id: int, data: dict):
    entity = model(empresa_id=empresa_id, **data)
    db.add(entity)
    db.commit()
    db.refresh(entity)
    return entity


def update_entity(db: Session, entity, data: dict):
    for key, value in data.items():
        if value is not None:
            setattr(entity, key, value)
    db.commit()
    db.refresh(entity)
    return entity
