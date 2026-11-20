"""Small, reviewable refinements for a mid-song handoff.

The helpers in this module combine independent evidence which is useful when
the two source windows are close in tempo but are not guaranteed to share a
confirmed downbeat.  They never stretch, pitch-shift, or claim a lyric/phrase
boundary.  A caller still has to render and audition the resulting plan.
"""
import math
from .grid_evidence import grid_review_reasons


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def phase_fraction(time, grid):
    """Return the fractional beat phase for a measured local grid.

    ``anchor`` is treated as an analysis anchor, not as a confirmed downbeat.
    ``None`` is returned when the grid is unavailable or invalid.
    """
    if not _finite(time) or not isinstance(grid, dict):
        return None
    if (grid.get('status', 'estimated')!='estimated' or
            grid_review_reasons({'grid':grid})):
        return None
    bpm, anchor = grid.get("bpm"), grid.get("anchor")
    if not (_finite(bpm) and 40 <= float(bpm) <= 240 and _finite(anchor)):
        return None
    beat = 60.0 / float(bpm)
    return ((float(time) - float(anchor)) / beat) % 1.0


def phase_alignment(left_time, left_grid, right_time, right_grid):
    """Describe whether two candidate boundaries land on a similar beat phase."""
    left = phase_fraction(left_time, left_grid)
    right = phase_fraction(right_time, right_grid)
    if left is None or right is None:
        return {
            "status": "unknown",
            "phase_distance_beats": None,
            "score": 0.35,
            "review_required": True,
            "reason": "至少一侧没有可用局部网格，保留人工复核。",
        }
    distance = abs(left - right)
    distance = min(distance, 1.0 - distance)
    # A half-beat separation is the worst case.  This is a compatibility
    # score only; it does not identify a downbeat or approve a cut.
    score = max(0.0, 1.0 - distance / 0.5)
    return {
        "status": "measured",
        "left_phase": round(left, 6),
        "right_phase": round(right, 6),
        "phase_distance_beats": round(distance, 6),
        "score": round(score, 5),
        "review_required": bool(distance > 0.18),
        "reason": "局部拍相位接近，可作为短交接的附加证据；仍不确认首拍或完整乐句。",
    }


def refined_score(base_score, hearing, phase, low_probe=None, seconds=None):
    """Add phase and rendered low-band evidence without hiding uncertainty."""
    smooth = hearing.get("comparison", {}).get("smoothness_score", 0.0)
    score = 0.62 * float(base_score) + 0.18 * float(phase.get("score", 0.35))
    if low_probe and low_probe.get("status") == "measured":
        classification = low_probe.get("classification")
        low_score = {"no_sustained_issue": 1.0, "coherent_buildup": 0.55,
                     "cancellation": 0.15}.get(classification, 0.35)
        # A clean probe is still better when the overlap is shorter.  The
        # small excess seen in a longer crossfade is therefore visible in the
        # ranking without pretending it predicts taste.
        excess = max(0.0, float(low_probe.get("peak_sum_excess_db", 0.0)))
        low_score *= max(0.0, 1.0 - min(excess / 1.5, 0.6))
        score += 0.12 * low_score
    else:
        score += 0.12 * 0.35
    # Preserve the local hearing signal and make a risky probe visible in the
    # ranking instead of allowing phase alignment to mask it.
    if "low_frequency_collision" in hearing.get("risk_flags", []):
        score -= 0.06
    if phase.get("review_required"):
        score -= 0.025
    if _finite(seconds):
        # Around three beats is a useful compromise for this dense pair:
        # shorter is a cut-like handoff, while a full four-beat overlap has
        # more opportunity for low-band buildup.
        duration_fit = max(0.0, 1.0 - abs(float(seconds) - 1.25) / 1.25)
        score += 0.08 * duration_fit
    return round(max(0.0, min(1.0, score)), 5)


def transition_profiles(bpm, *, phase_score=0.5):
    """Return conservative crossfade lengths for a near-tempo handoff."""
    beat = 60.0 / float(bpm) if _finite(bpm) and float(bpm) > 0 else 0.5
    # The short option protects vocals and dense low end; the four-beat option
    # is exposed only when phase evidence is reasonably close.
    profiles = [(round(2.0 * beat, 4), "micro_2beat"),
                (round(2.7 * beat, 4), "short_blend")]
    if phase_score >= 0.72:
        profiles.append((round(min(2.0, 4.0 * beat), 4), "phase_locked_4beat"))
    return [{"seconds": seconds, "label": label, "beats": round(seconds / beat, 3)}
            for seconds, label in profiles]


def choose_transition_seconds(bpm, *, tempo_gap=None, phase_score=0.35,
                              grid_review=True, style="journey"):
    """Choose a bounded join length from local evidence without changing speed."""
    profiles = transition_profiles(bpm, phase_score=phase_score)
    if not profiles:
        return 1.0
    gap = float(tempo_gap) if _finite(tempo_gap) else 1.0
    if grid_review or gap > 0.025 or style == "rising":
        return profiles[0]["seconds"]
    return profiles[1]["seconds"] if phase_score >= 0.72 and len(profiles) > 1 else profiles[0]["seconds"]
