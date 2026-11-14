"""Lakeview Data 门户的 mock 数据集内容。"""

from __future__ import annotations


def _f(name: str, type_: str, description: str) -> dict:
    return {"name": name, "type": type_, "description": description}


def _r(fmt: str, size: str, href: str) -> dict:
    return {"format": fmt, "size": size, "href": href}


GAMMA_DATASETS = [
    {
        "id": "g-001",
        "title": "Lakeview Water Quality Samples",
        "category": "Environment",
        "publisher": "Lakeview Water Board",
        "temporal": "2020-01-01 to 2023-12-31",
        "geography": "Minnesota",
        "license": "CC0",
        "frequency": "Quarterly",
        "description": "Grab samples from lakes and streams in the Lakeview region.",
        "fields": [
            _f("sample_id", "string", "Sample identifier"),
            _f("lake_name", "string", "Water body name"),
            _f("ph_level", "float", "pH measurement"),
            _f("sampled_at", "datetime", "Sample collection time"),
        ],
        "resources": [_r("CSV", "4.5 MB", "/gamma/files/g-001.csv")],
    },
    {
        "id": "g-002",
        "title": "Regional Air Emissions Inventory",
        "category": "Air Quality",
        "publisher": "Lakeview Environmental Agency",
        "temporal": "2023",
        "geography": "Minnesota",
        "license": "CC BY 4.0",
        "frequency": "Annually",
        "description": "Facility-level annual emissions reported to the regional inventory.",
        "fields": [
            _f("facility_id", "string", "Facility identifier"),
            _f("pollutant", "string", "Pollutant name"),
            _f("tons", "decimal", "Reported short tons"),
            _f("year", "integer", "Reporting year"),
        ],
        "resources": [_r("JSON", "9.9 MB", "/gamma/files/g-002.json")],
    },
    {
        "id": "g-003",
        "title": "Public Transit On-Time Performance",
        "category": "Transit",
        "publisher": "Lakeview Transit",
        "temporal": "2023-01-01 to 2023-06-30",
        "geography": "Minnesota",
        "license": "CC-BY-4.0",
        "frequency": "Daily",
        "description": "Vehicle trip arrival performance for the first half of 2023.",
        "fields": [
            _f("trip_id", "string", "Trip identifier"),
            _f("route", "string", "Route short name"),
            _f("delay_seconds", "integer", "Arrival delay in seconds"),
            _f("status", "string", "On-time, late, or cancelled"),
        ],
        "resources": [_r("CSV", "15.2 MB", "/gamma/files/g-003.csv")],
    },
    {
        "id": "g-004",
        "title": "School Enrollment by Grade",
        "category": "Education",
        "publisher": "Lakeview School District",
        "temporal": "2019 to 2023",
        "geography": "Minnesota",
        "license": "CC-BY-4.0",
        "frequency": "Yearly",
        "description": "Fall enrollment counts by school, grade, and school year.",
        "fields": [
            _f("school_id", "string", "School identifier"),
            _f("grade", "string", "Grade label"),
            _f("enrolled", "integer", "Enrollment count"),
            _f("school_year", "string", "School year label"),
        ],
        "resources": [_r("XLSX", "760 KB", "/gamma/files/g-004.xlsx")],
    },
    {
        "id": "g-005",
        "title": "Recycling Diversion Rates",
        "category": "Utilities / Waste",
        "publisher": "Lakeview Sustainability Office",
        "temporal": "2022-01-01 to 2022-12-31",
        "geography": "Minnesota",
        "license": "CC-BY-4.0",
        "frequency": "Monthly",
        "description": "Monthly material recovery and diversion rates by facility.",
        "fields": [
            _f("facility", "string", "Processing facility"),
            _f("material", "string", "Material category"),
            _f("tons_recycled", "float", "Short tons recycled"),
            _f("diversion_rate", "float", "Diversion rate percentage"),
        ],
        "resources": [_r("CSV", "3.4 MB", "/gamma/files/g-005.csv")],
    },
    {
        "id": "g-006",
        "title": "Stormwater Sensor Calibration Logs",
        "category": "Environment",
        "publisher": "Lakeview Water Board",
        "temporal": "2023-12-31 to 2023-01-01",
        "geography": "Minnesota",
        "license": "CC-BY-4.0",
        "frequency": "As needed",
        "description": "Draft calibration register retained for review; dates are known to be reversed.",
        "fields": [
            _f("sensor_id", "string", "Sensor identifier"),
            _f("reading", "mystery", "Raw calibration reading"),
            _f("calibrated_at", "datetime", "Calibration completion time"),
        ],
        "resources": [_r("CSV", "unknown size", "/gamma/files/g-006.csv")],
    },
]
