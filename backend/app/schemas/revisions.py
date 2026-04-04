# app/schemas/revisions.py
from datetime import datetime, date, time

from pydantic import BaseModel, Field, field_validator


class RevisionCreate(BaseModel):
    # VEHICULO
    tipo_vehiculo: str | None = Field(default=None, max_length=30)
    marca: str | None = Field(default=None, max_length=50)
    modelo: str | None = Field(default=None, max_length=50)
    anio: int | None = None
    link_compra: str | None = None
    presupuesto_compra: int | None = None
    vendedor_tipo: str | None = Field(default=None, max_length=20)
    tipo_vendedor: str | None = Field(default=None, max_length=20)
    agencia_id: int | None = None
    compro: str | None = Field(default=None, max_length=12)
    resultado_link: str | None = None
    comision: int | None = None
    cobrado: str | None = Field(default=None, max_length=5)
    fecha_cobro: date | None = None

    # ZONA + DIRECCION
    zone_group: str | None = Field(default=None, max_length=50)
    zone_detail: str | None = Field(default=None, max_length=80)
    direccion_texto: str | None = None
    link_maps: str | None = None
    direccion_estado: str | None = Field(default=None, max_length=20)

    # PRESUPUESTO / PAGO
    precio_base: int | None = None
    viaticos: int | None = None
    precio_total: int | None = None
    pago: bool | None = None
    medio_pago: str | None = Field(default=None, max_length=20)

    # TURNO (date + time)
    turno_fecha: date | None = None
    turno_hora: time | None = None
    cliente_presente: bool | None = None
    turno_notas: str | None = None

    # ESTADO
    estado_revision: str | None = Field(default=None, max_length=20)
    resultado: str | None = Field(default=None, max_length=20)
    motivo_rechazo: str | None = Field(default=None, max_length=80)

    @field_validator("cobrado")
    @classmethod
    def validate_cobrado(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().upper()
        if normalized not in {"SI", "NO"}:
            raise ValueError('cobrado inválido: debe ser "SI" o "NO"')
        return normalized

    @field_validator("compro")
    @classmethod
    def validate_compro(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().upper()
        if normalized not in {"SI", "NO", "OFRECIDO"}:
            raise ValueError('compro inválido: debe ser "SI", "NO" o "OFRECIDO"')
        return normalized


class RevisionSummaryOut(BaseModel):
    id: int
    created_at: datetime
    estado_revision: str
    resultado: str | None = None
    motivo_rechazo: str | None = None
    precio_total: int | None = None

    class Config:
        from_attributes = True


class RevisionUpdate(RevisionCreate):
    recalcular_presupuesto: bool = True


class RevisionOut(BaseModel):
    id: int
    lead_id: int
    created_at: datetime

    tipo_vehiculo: str | None = None
    marca: str | None = None
    modelo: str | None = None
    anio: int | None = None
    link_compra: str | None = None
    presupuesto_compra: int | None = None
    vendedor_tipo: str | None = None
    tipo_vendedor: str | None = None
    agencia_id: int | None = None
    compro: str | None = None
    resultado_link: str | None = None
    comision: int | None = None
    cobrado: str | None = None
    fecha_cobro: date | None = None

    zone_group: str | None = None
    zone_detail: str | None = None
    direccion_texto: str | None = None
    link_maps: str | None = None
    direccion_estado: str | None = None

    precio_base: int | None = None
    viaticos: int | None = None
    precio_total: int | None = None
    pago: bool | None = None
    medio_pago: str | None = None

    turno_fecha: date | None = None
    turno_hora: time | None = None
    cliente_presente: bool | None = None
    turno_notas: str | None = None

    estado_revision: str
    resultado: str | None = None
    motivo_rechazo: str | None = None

    class Config:
        from_attributes = True
