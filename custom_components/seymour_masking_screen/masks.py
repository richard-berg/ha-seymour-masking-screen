"""Masks and groups of masks of a Seymour masking screen."""

from collections.abc import Iterable, Sequence

from seymourlib.protocol import MotorID

# The controller moves both masks of a pair with a single motor ID.
MASK_PAIRS: dict[MotorID, tuple[MotorID, MotorID]] = {
    MotorID.VERTICAL: (MotorID.TOP, MotorID.BOTTOM),
    MotorID.HORIZONTAL: (MotorID.LEFT, MotorID.RIGHT),
}

# A mask within this many percent of full retraction counts as at home, which
# absorbs small encoder offsets.
HOME_TOLERANCE = 1.0


def mask_targets(mask_ids: Sequence[MotorID]) -> list[tuple[MotorID, list[MotorID]]]:
    """Return each motor ID to control, with the masks it moves.

    Every mask is a target. Both masks of a pair are a target when the screen
    also has other masks, and all masks are a target when there are several.
    """
    targets = [(mask, [mask]) for mask in mask_ids]
    for pair, members in MASK_PAIRS.items():
        # A screen with only this pair is already covered by all masks
        if set(members) <= set(mask_ids) and len(mask_ids) > len(members):
            targets.append((pair, list(members)))
    if len(mask_ids) > 1:
        targets.append((MotorID.ALL, list(mask_ids)))
    return targets


def is_at_home(positions: dict[MotorID, float], masks: Iterable[MotorID]) -> bool:
    """Return whether all given masks are fully retracted."""
    return all(
        (position := positions.get(mask)) is not None and position <= HOME_TOLERANCE
        for mask in masks
    )
