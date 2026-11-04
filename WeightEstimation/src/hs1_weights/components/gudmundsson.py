"""Gudmundsson, *General Aviation Aircraft Design* (2014), Ch. 6 structure methods.

Doc 06 §4 asks for Gudmundsson "where it differs" from Raymer, USAF and Cessna. Which equations differ cannot
be determined without the project's copy of the text, so **no Gudmundsson methods are registered yet**.
Tracked as doc 12 SC-9. Add each method here with its edition and equation number, using the registry
decorator ``components.base.component`` and the same imperial-core / SI-wrapper pattern as ``raymer_ga``.
"""
from __future__ import annotations

REGISTERED: tuple[str, ...] = ()
