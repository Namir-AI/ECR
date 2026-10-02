"""Approved package Series and drivetrain applicability (no legacy conversion)."""

COOLING_TOWER_SERIES = (
    "AQ-3800",
    "CF-I",
    "CF-II",
    "CF-III",
    "6.1 KF",
    "9 KF",
    "RXF",
    "Series 9",
    "Series 10",
    "Series 15",
    "Series 18",
)


def applicable_sections(series: str) -> frozenset[str]:
    if series == "AQ-3800":
        return frozenset({"Bearing Housing", "Belt & Pulleys"})
    if series in COOLING_TOWER_SERIES and series != "CF-I":
        return frozenset({"Gearboxes", "Drive Shafts"})
    # Unknown legacy Series is not silently assigned an engineering classification.
    return frozenset()


CONDITIONAL_SECTIONS = frozenset(
    {"Gearboxes", "Drive Shafts", "Bearing Housing", "Belt & Pulleys"}
)


def active_sections(sections, series: str):
    applicable = applicable_sections(series)
    return tuple(
        (section, fields)
        for section, fields in sections
        if section not in CONDITIONAL_SECTIONS or section in applicable
    )


def active_fields(sections, series: str) -> frozenset[str]:
    return frozenset(
        name for _, fields in active_sections(sections, series) for name, *_ in fields
    )
