"""Canonical framework short_name sets — import these instead of repeating literals."""

from __future__ import annotations

ISO_FRAMEWORKS: frozenset[str] = frozenset({"iso27001", "iso20000", "iso9001"})
CMMC_FRAMEWORKS: frozenset[str] = frozenset({"cmmc_l2", "cmmc"})
DPA_FRAMEWORKS: frozenset[str] = frozenset({"dpa_attachment_c"})
ATO_FRAMEWORKS: frozenset[str] = frozenset({"nist_800_53"})
# Loaded as a framework row but excluded from dashboard/framework list UIs.
NON_CATALOG_FRAMEWORKS: frozenset[str] = frozenset({"obligations"})

FRAMEWORK_DISPLAY_NAMES: dict[str, str] = {
    "iso27001": "ISO/IEC 27001:2022",
    "iso20000": "ISO/IEC 20000-1:2018",
    "iso9001": "ISO 9001:2015",
    "cmmc_l2": "CMMC Level 2",
}
