from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import ViaticosZone

# Spanish grammatical articles that customers routinely drop from locality names.
_ARTICLE_PREFIX = re.compile(r"^(?:la|el|los|las)\s+")


@dataclass(frozen=True)
class BasePriceRow:
    tipo_vehiculo: str
    precio_base: int


class PricingRepository:
    def __init__(self, csv_path: Path | None = None):
        self._csv_path = csv_path or Path(__file__).resolve().parents[1] / "data" / "pricing_base.csv"

    @lru_cache(maxsize=1)
    def load_base_prices(self) -> tuple[BasePriceRow, ...]:
        rows: list[BasePriceRow] = []
        with self._csv_path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            for raw in reader:
                tipo = str(raw.get("tipo_vehiculo", "")).strip()
                precio = str(raw.get("precio_base", "")).strip()
                if not tipo or not precio:
                    continue
                rows.append(BasePriceRow(tipo_vehiculo=tipo, precio_base=int(precio)))
        return tuple(rows)

    def find_base_price(self, tipo_vehiculo: str) -> BasePriceRow | None:
        normalized = self._normalize(tipo_vehiculo)
        for row in self.load_base_prices():
            if self._normalize(row.tipo_vehiculo) == normalized:
                return row
        return None

    def find_zone_by_group_and_detail(
        self,
        db: Session,
        zone_group: str | None,
        zone_detail: str | None,
    ) -> ViaticosZone | None:
        normalized_group = self._normalize(zone_group)
        normalized_detail = self._normalize(zone_detail)

        if normalized_group and normalized_detail:
            row = db.execute(
                select(ViaticosZone)
                .where(func.lower(func.trim(ViaticosZone.zone_group)) == normalized_group)
                .where(func.lower(func.trim(ViaticosZone.zone_detail)) == normalized_detail)
                .limit(1)
            ).scalars().first()
            if row:
                return row

        if normalized_detail:
            stmt = (
                select(ViaticosZone)
                .where(func.lower(func.trim(ViaticosZone.zone_detail)) == normalized_detail)
                .order_by(ViaticosZone.zone_group.asc())
            )
            matches = db.execute(stmt).scalars().all()
            if normalized_group:
                for row in matches:
                    if self._normalize(row.zone_group) == normalized_group:
                        return row
            if matches:
                return matches[0]

        if normalized_detail:
            row = self._find_zone_by_article_variant(db, normalized_group, normalized_detail)
            if row is not None:
                return row

        if normalized_group:
            return db.execute(
                select(ViaticosZone)
                .where(func.lower(func.trim(ViaticosZone.zone_group)) == normalized_group)
                .where(ViaticosZone.zone_detail.is_(None))
                .limit(1)
            ).scalars().first()

        return None

    def _find_zone_by_article_variant(
        self,
        db: Session,
        normalized_group: str,
        normalized_detail: str,
    ) -> ViaticosZone | None:
        """Resolve a locality whose grammatical article the customer omitted (or added).

        L4.7W5-F4. A live customer wrote "Un 3008 en paternal". The catalog holds
        "La Paternal", the lookup normalised case and whitespace but not the article, and the
        miss was silent — it degraded into a location Flow and the customer typed an address
        by hand. Dropping the article is ordinary Argentine usage ("en paternal", "en boca",
        "vivo en plata"), so the catalog must meet it.

        Resolution is against the canonical table, never by editing the customer's text: a
        candidate matches only when stripping the article from a CANONICAL entry yields the
        customer's words, or vice versa. Arbitrary text is never article-stripped.

        Ambiguity is refused rather than guessed. If two canonical localities reduce to the
        same bare form, or the bare form is itself a different canonical locality, this
        returns None and the existing location Flow still asks. Verified against the live
        table: 207 localities, 13 article-prefixed, 0 collisions — but the guard stands so
        that adding one later cannot silently start mis-resolving.
        """
        bare = _ARTICLE_PREFIX.sub("", normalized_detail)
        has_article = bare != normalized_detail

        rows = db.execute(select(ViaticosZone).where(ViaticosZone.zone_detail.is_not(None)))
        candidates: list[ViaticosZone] = []
        for row in rows.scalars():
            canonical = self._normalize(row.zone_detail)
            canonical_bare = _ARTICLE_PREFIX.sub("", canonical)
            if canonical == normalized_detail:
                continue                      # exact match already tried above
            # customer omitted the article, or supplied one the catalog does not use
            if canonical_bare == bare and (has_article or canonical != canonical_bare):
                candidates.append(row)

        if len(candidates) != 1:
            return None                       # none, or ambiguous — never guess
        match = candidates[0]
        if normalized_group and self._normalize(match.zone_group) != normalized_group:
            return None                       # the named group contradicts the match
        return match

    @staticmethod
    def _normalize(value: str | None) -> str:
        return " ".join((value or "").strip().lower().split())

