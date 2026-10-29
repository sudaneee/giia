"""
Mid-term assessment achievement key, per session.

A session's `midterm_max_score` (see Session model) is the maximum a
subject's mid-term score can be for that session. Each max score maps to
its own 5-band achievement key below - this is deliberately a lookup table,
not a formula that evenly divides max_score into 5, since the bands are a
pedagogical/policy decision the school makes (the original 20-point bands
below aren't evenly sized either: 7, 3, 3, 4, 4 scores wide).

Add an entry here whenever the school adopts a new max score; everything
else (validation, tick marks, the "Overall Effort" label) reads from this
table instead of a hardcoded scale, so it only has to change in one place.
"""

# max_score -> [(label, low, high), ...], low/high inclusive, covering
# 0..max_score with no gaps and no overlaps.
MIDTERM_BANDS_BY_MAX_SCORE = {
    20: [
        ('Emerging', 0, 6),
        ('Emerging +', 7, 9),
        ('Expected', 10, 12),
        ('Expected +', 13, 16),
        ('Exceeding', 17, 20),
    ],
    10: [
        ('Emerging', 0, 2),
        ('Emerging +', 3, 4),
        ('Expected', 5, 6),
        ('Expected +', 7, 8),
        ('Exceeding', 9, 10),
    ],
}

# The descriptive suffix shown next to the label on the "Overall Effort"
# line (e.g. "Emerging (Need Support)") - same wording regardless of
# max_score, since it describes the band, not the raw numbers.
BAND_DESCRIPTIONS = {
    'Emerging': 'Need Support',
    'Emerging +': 'Satisfactory',
    'Expected': 'Good',
    'Expected +': 'Very Good',
    'Exceeding': 'Excellent',
}


def get_midterm_bands(max_score):
    """
    The 5 (label, low, high) achievement bands for a mid-term score out of
    `max_score`. Raises ValueError for a max_score with no defined key,
    rather than guessing a split - add it to MIDTERM_BANDS_BY_MAX_SCORE
    first.
    """
    try:
        return MIDTERM_BANDS_BY_MAX_SCORE[max_score]
    except KeyError:
        raise ValueError(
            f"No mid-term achievement key is defined for a max score of {max_score}. "
            f"Add one to MIDTERM_BANDS_BY_MAX_SCORE in src/midterm_grading.py."
        )


def label_for_score(score, bands):
    """The band label a score falls into, or None if score is out of range
    for every band (e.g. bad data above max_score) - callers should treat
    None as "Invalid Score" rather than guessing the nearest band."""
    for label, low, high in bands:
        if low <= score <= high:
            return label
    return None


def describe_label(label):
    """'Emerging' -> 'Emerging (Need Support)'; passes through unknown labels unchanged."""
    if label is None:
        return 'Invalid Score'
    description = BAND_DESCRIPTIONS.get(label)
    return f'{label} ({description})' if description else label
