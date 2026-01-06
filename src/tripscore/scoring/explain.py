from __future__ import annotations

from tripscore.domain.models import ScoreBreakdown


def one_line_summary(breakdown: ScoreBreakdown) -> str:
    parts = [f"total={breakdown.total_score:.3f}"]
    for comp in breakdown.components:
        parts.append(f"{comp.name}={comp.score:.3f} (w={comp.weight:.2f})")
    return " | ".join(parts)
