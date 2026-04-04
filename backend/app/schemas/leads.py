from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from .revisions import RevisionSummaryOut


class LeadCreate(BaseModel):
    telefono: str | None = Field(default=None, max_length=40)
    nombre: str | None = Field(default=None, max_length=80)
    apellido: str | None = Field(default=None, max_length=80)
    email: str | None = Field(default=None, max_length=120)
    canal: str | None = Field(default=None, max_length=50)
    compro_el_auto: str | None = Field(default=None, max_length=10)

    @field_validator("compro_el_auto")
    @classmethod
    def validate_compro_el_auto(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().upper()
        if normalized not in {"SI", "NO"}:
            raise ValueError('compro_el_auto inválido: debe ser "SI" o "NO"')
        return normalized


class LeadUpdate(BaseModel):
    estado: str | None = Field(default=None, examples=["COORDINAR_DISPONIBILIDAD"], max_length=40)
    motivo_perdida: str | None = Field(default=None, examples=["PRECIO"], max_length=30)
    necesita_humano: bool | None = Field(default=None, examples=[True])
    flag: str | None = Field(default=None, examples=["PRESUPUESTO_ENVIADO"], max_length=40)
    telefono: str | None = Field(default=None, max_length=40)
    nombre: str | None = Field(default=None, max_length=80)
    apellido: str | None = Field(default=None, max_length=80)
    email: str | None = Field(default=None, max_length=120)
    canal: str | None = Field(default=None, max_length=50)
    compro_el_auto: str | None = Field(default=None, max_length=10)

    @field_validator("compro_el_auto")
    @classmethod
    def validate_compro_el_auto(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().upper()
        if normalized not in {"SI", "NO"}:
            raise ValueError('compro_el_auto inválido: debe ser "SI" o "NO"')
        return normalized


class LeadOut(BaseModel):
    id: int
    created_at: datetime
    estado: str
    flag: str | None = None

    telefono: str | None = None
    nombre: str | None = None
    apellido: str | None = None

    necesita_humano: bool
    motivo_perdida: str | None = None

    class Config:
        from_attributes = True


class LeadDetailOut(LeadOut):
    latest_revision: RevisionSummaryOut | None = None
