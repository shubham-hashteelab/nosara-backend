"""
Trade taxonomy for the Nosara contractor role system.

All trade values are plain strings (no PostgreSQL enum) per project convention.
This module is the single source of truth — validated app-side before any
write to the database.
"""

VALID_TRADES: set[str] = {
    "PLUMBING",
    "ELECTRICAL",
    "PAINTING",
    "CARPENTRY",
    "TILING",
    "CIVIL",
    "HVAC",
    "MISC",
}

VALID_SNAG_IMAGE_KINDS: set[str] = {"NC", "CLOSURE"}


def is_valid_trade(value: str) -> bool:
    return value in VALID_TRADES


def is_valid_snag_image_kind(value: str) -> bool:
    return value in VALID_SNAG_IMAGE_KINDS
