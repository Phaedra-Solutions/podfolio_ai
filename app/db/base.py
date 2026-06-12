from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


# Model imports live in app/db/base_all.py to avoid circular imports
