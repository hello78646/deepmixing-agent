"""Respect explicit uncertainty across consumers of local beat evidence.

Older reports may omit phase/layer diagnostics. This helper does not upgrade
them or identify downbeats; it preserves their existing compatibility behavior.
"""


def grid_review_reasons(report):
    grid = report.get('grid', {})
    reasons = []
    for field, code in ((None, 'grid_review'), ('diagnostics', 'grid_fit_review'),
                        ('phase_drift', 'phase_drift_review')):
        evidence = grid if field is None else grid.get(field, {})
        if evidence.get('review_required'):
            reasons.append(code)
    layer = grid.get('tempo_layer', {})
    # The estimator marks every base layer as advisory. That alone does not
    # diagnose an octave error. Only an explicitly selected, unresolved alias
    # blocks automatic use here; no threshold is tuned to this evaluation set.
    if layer.get('layer') in ('half_candidate', 'double_candidate') and layer.get('review_required', True):
        reasons.append('tempo_layer_review')
    for field in ('tempo_changes', 'beat_drift_candidates'):
        if report.get(field):
            reasons.append(field)
    return reasons
