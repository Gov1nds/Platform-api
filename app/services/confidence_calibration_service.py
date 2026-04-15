from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
from typing import Iterable

from sqlalchemy.orm import Session

from app.models.outcomes import ConfidenceCalibrationData, QuoteOutcome


DECIMAL_ZERO = Decimal("0")
DECIMAL_ONE = Decimal("1")
DECIMAL_BAND_WIDTH = Decimal("0.10")
DECIMAL_PRECISION = Decimal("0.000001")


@dataclass
class CalibrationMappingResult:
    raw_confidence: Decimal
    calibrated_confidence: Decimal
    used_calibration: bool
    fallback_reason: str | None
    band_sample_size: int
    score_range_min: Decimal | None
    score_range_max: Decimal | None


class ConfidenceCalibrationService:
    """Deterministic confidence calibration using historical recommendation outcomes."""

    default_band_width = DECIMAL_BAND_WIDTH
    minimum_sample_size = 3

    def _utcnow(self) -> datetime:
        return datetime.now(timezone.utc)

    def _to_decimal(self, value) -> Decimal | None:
        if value is None or value == "":
            return None
        try:
            return Decimal(str(value))
        except Exception:
            return None

    def _normalize_score(self, value) -> Decimal | None:
        dec = self._to_decimal(value)
        if dec is None:
            return None
        if dec > DECIMAL_ONE and dec <= Decimal("100"):
            dec = dec / Decimal("100")
        if dec < DECIMAL_ZERO:
            dec = DECIMAL_ZERO
        if dec > DECIMAL_ONE:
            dec = DECIMAL_ONE
        return dec.quantize(DECIMAL_PRECISION)

    def _quantize(self, value: Decimal | None) -> Decimal | None:
        if value is None:
            return None
        return value.quantize(DECIMAL_PRECISION)

    def _band_start(self, score: Decimal, band_width: Decimal) -> Decimal:
        if score >= DECIMAL_ONE:
            return DECIMAL_ONE - band_width
        bucket = (score / band_width).to_integral_value(rounding=ROUND_DOWN)
        return (bucket * band_width).quantize(DECIMAL_PRECISION)

    def _extract_score(self, row: QuoteOutcome) -> Decimal | None:
        metadata = row.source_metadata or {}
        for key in (
            "recommendation_score",
            "raw_recommendation_score",
            "raw_score",
            "confidence_score",
            "recommended_confidence_score",
        ):
            if key in metadata:
                score = self._normalize_score(metadata.get(key))
                if score is not None:
                    return score
        return None

    def _iter_observations(self, db: Session) -> Iterable[tuple[Decimal, bool]]:
        rows = db.query(QuoteOutcome).all()
        for row in rows:
            score = self._extract_score(row)
            if score is None:
                continue
            yield score, bool(row.is_accepted)

    def rebuild_calibration_data(
        self,
        db: Session,
        *,
        band_width: Decimal | float | str | None = None,
        minimum_sample_size: int | None = None,
        calculated_at: datetime | None = None,
    ) -> list[ConfidenceCalibrationData]:
        width = self._normalize_score(band_width if band_width is not None else self.default_band_width) or self.default_band_width
        if width <= DECIMAL_ZERO:
            width = self.default_band_width
        min_samples = minimum_sample_size or self.minimum_sample_size
        calculated_at = calculated_at or self._utcnow()

        bands: dict[Decimal, dict[str, int]] = {}
        cursor = Decimal("0.00")
        while cursor < DECIMAL_ONE:
            bands[cursor.quantize(DECIMAL_PRECISION)] = {"sample_size": 0, "success_count": 0}
            cursor += width

        for score, success in self._iter_observations(db):
            start = self._band_start(score, width)
            bucket = bands.setdefault(start, {"sample_size": 0, "success_count": 0})
            bucket["sample_size"] += 1
            bucket["success_count"] += 1 if success else 0

        created: list[ConfidenceCalibrationData] = []
        for start in sorted(bands.keys()):
            end = min(DECIMAL_ONE, start + width).quantize(DECIMAL_PRECISION)
            sample_size = bands[start]["sample_size"]
            success_count = bands[start]["success_count"]
            historical_success_rate = None
            calibrated_probability = None
            if sample_size > 0:
                historical_success_rate = self._quantize(Decimal(success_count) / Decimal(sample_size))
                if sample_size >= min_samples:
                    calibrated_probability = historical_success_rate

            row = ConfidenceCalibrationData(
                calibration_id=f"calibration-{calculated_at.strftime('%Y%m%d%H%M%S')}-{str(start).replace('.', '')}",
                score_range_min=start,
                score_range_max=end,
                sample_size=sample_size,
                historical_success_rate=historical_success_rate,
                calibrated_probability=calibrated_probability,
                calculated_at=calculated_at,
            )
            db.add(row)
            created.append(row)
        db.flush()
        return created

    def get_latest_calibration_bands(self, db: Session) -> list[ConfidenceCalibrationData]:
        latest = db.query(ConfidenceCalibrationData.calculated_at).order_by(ConfidenceCalibrationData.calculated_at.desc()).first()
        if not latest or latest[0] is None:
            return []
        return (
            db.query(ConfidenceCalibrationData)
            .filter(ConfidenceCalibrationData.calculated_at == latest[0])
            .order_by(ConfidenceCalibrationData.score_range_min.asc())
            .all()
        )

    def map_confidence(
        self,
        db: Session,
        *,
        raw_confidence: Decimal | float | str | None,
        minimum_sample_size: int | None = None,
    ) -> CalibrationMappingResult:
        raw = self._normalize_score(raw_confidence)
        if raw is None:
            raw = DECIMAL_ZERO
        min_samples = minimum_sample_size or self.minimum_sample_size
        bands = self.get_latest_calibration_bands(db)
        if not bands:
            return CalibrationMappingResult(
                raw_confidence=raw,
                calibrated_confidence=raw,
                used_calibration=False,
                fallback_reason="calibration_missing",
                band_sample_size=0,
                score_range_min=None,
                score_range_max=None,
            )

        selected = None
        for band in bands:
            band_min = self._to_decimal(band.score_range_min) or DECIMAL_ZERO
            band_max = self._to_decimal(band.score_range_max) or DECIMAL_ONE
            if raw >= band_min and (raw < band_max or band_max == DECIMAL_ONE):
                selected = band
                break
        if selected is None:
            selected = bands[-1]

        band_min = self._to_decimal(selected.score_range_min) or DECIMAL_ZERO
        band_max = self._to_decimal(selected.score_range_max) or DECIMAL_ONE
        calibrated = self._to_decimal(selected.calibrated_probability)
        if selected.sample_size < min_samples or calibrated is None:
            return CalibrationMappingResult(
                raw_confidence=raw,
                calibrated_confidence=raw,
                used_calibration=False,
                fallback_reason="insufficient_sample_size",
                band_sample_size=selected.sample_size,
                score_range_min=band_min,
                score_range_max=band_max,
            )
        return CalibrationMappingResult(
            raw_confidence=raw,
            calibrated_confidence=calibrated,
            used_calibration=True,
            fallback_reason=None,
            band_sample_size=selected.sample_size,
            score_range_min=band_min,
            score_range_max=band_max,
        )


confidence_calibration_service = ConfidenceCalibrationService()