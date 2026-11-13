"""内置 mock 数据门户的静态数据定义。

数据只用于离线演示与测试，不指向任何真实机构、域名或数据集。
资源链接均为本站回环路径，采集器只记录元数据，不下载二进制内容。
"""

from __future__ import annotations


def _f(name: str, type_: str, description: str) -> dict:
    return {"name": name, "type": type_, "description": description}


def _r(fmt: str, size: str, href: str) -> dict:
    return {"format": fmt, "size": size, "href": href}


ALPHA_DATASETS = [
    {
        "id": "air-quality-2023",
        "title": "Air Quality Monitoring Stations 2023",
        "category": "Environment",
        "publisher": "Atlas Environmental Agency",
        "temporal": "2023-01-01 to 2023-12-31",
        "geography": "United States",
        "license": "CC-BY-4.0",
        "frequency": "Daily",
        "description": "Hourly pollutant readings collected by fixed monitoring stations.",
        "fields": [
            _f("station_id", "string", "Stable station identifier"),
            _f("pm25", "number", "Fine particulate matter concentration"),
            _f("observed_at", "datetime", "Observation timestamp in UTC"),
            _f("region", "string", "Administrative region label"),
        ],
        "resources": [
            _r("CSV", "12.4 MB", "/alpha/files/air-quality-2023.csv"),
            _r("JSON", "3.1 MB", "/alpha/files/air-quality-2023.json"),
        ],
    },
    {
        "id": "urban-tree-canopy-2022",
        "title": "Urban Tree Canopy Survey 2022",
        "category": "Environment / Climate",
        "publisher": "Atlas Parks Department",
        "temporal": "2022-01-01 to 2022-12-31",
        "geography": "California",
        "license": "Public Domain",
        "frequency": "Annual",
        "description": "Street tree inventory with species and canopy measurements.",
        "fields": [
            _f("tree_id", "text", "Inventory tree identifier"),
            _f("species", "string", "Common species name"),
            _f("dbh_cm", "decimal", "Diameter at breast height, centimetres"),
            _f("planted_date", "date", "Planting date when known"),
            _f("geometry", "geojson", "Point geometry in WGS84"),
        ],
        "resources": [_r("GeoJSON", "8.2 MB", "/alpha/files/tree-canopy-2022.geojson")],
    },
    {
        "id": "metro-transit-ridership",
        "title": "Metro Transit Ridership Monthly",
        "category": "Transportation",
        "publisher": "Atlas Transit Authority",
        "temporal": "2021-01-01 to 2023-12-31",
        "geography": "New York",
        "license": "CC0-1.0",
        "frequency": "Monthly",
        "description": "Monthly boardings aggregated by route and service mode.",
        "fields": [
            _f("route_id", "string", "Transit route identifier"),
            _f("month", "date", "Service month, first day"),
            _f("ridership", "integer", "Total monthly boardings"),
            _f("mode", "string", "Bus, rail, or ferry"),
        ],
        "resources": [_r("CSV", "45.7 MB", "/alpha/files/ridership.csv")],
    },
    {
        "id": "municipal-budget-fy2023",
        "title": "Municipal Budget Allocations FY2023",
        "category": "Government / Finance",
        "publisher": "Atlas Budget Office",
        "temporal": "2022-07-01 to 2023-06-30",
        "geography": "California",
        "license": "CC-BY-4.0",
        "frequency": "Annual",
        "description": "Adopted budget lines by department, fund, and fiscal year.",
        "fields": [
            _f("department", "string", "Responsible department"),
            _f("fund", "string", "Accounting fund name"),
            _f("amount_usd", "decimal", "Allocated amount in US dollars"),
            _f("fiscal_year", "string", "Two-digit fiscal year label"),
        ],
        "resources": [_r("XLSX", "2.0 MB", "/alpha/files/budget-fy2023.xlsx")],
    },
    {
        "id": "public-library-checkouts-2023",
        "title": "Public Library Checkouts 2023",
        "category": "Education / Culture",
        "publisher": "Atlas Public Library",
        "temporal": "2023-01-01 to 2023-12-31",
        "geography": "California",
        "license": "CC-BY-4.0",
        "frequency": "Monthly",
        "description": "Checkout counts by branch, title, and month.",
        "fields": [
            _f("branch", "string", "Library branch name"),
            _f("title", "string", "Catalogue title"),
            _f("checkout_count", "integer", "Checkouts in the month"),
            _f("month", "date", "Reporting month"),
        ],
        "resources": [_r("CSV", "6.8 MB", "/alpha/files/library-checkouts.csv")],
    },
]

BETA_DATASETS = [
    {
        "id": "hde-001",
        "title": "Air Quality Monitoring Stations (2023)",
        "category": "Environmental monitoring",
        "publisher": "Atlas Environmental Agency",
        "temporal": "2023-01-01/2023-12-31",
        "geography": "United States",
        "license": "CC BY 4.0",
        "frequency": "Every day",
        "description": "Cross-agency air quality extract, harmonised by Harbor Data Exchange.",
        "fields": [
            _f("station_id", "String", "Station code"),
            _f("pollutant", "String", "Measured pollutant"),
            _f("value", "Float", "Measured concentration"),
            _f("unit", "String", "Measurement unit"),
            _f("observed_at", "Timestamp", "Observation time"),
            _f("latitude", "Float", "Station latitude"),
            _f("longitude", "Float", "Station longitude"),
            _f("source_system", "String", "Originating telemetry system"),
        ],
        "resources": [
            _r("CSV", "18,432,000 bytes", "/beta/files/hde-001.csv"),
            _r("JSON", "3.2 MB", "/beta/files/hde-001.json"),
        ],
    },
    {
        "id": "hde-002",
        "title": "Harbor Ferry Passenger Counts",
        "category": "Transport",
        "publisher": "Harbor Port Authority",
        "temporal": "2023-01-01/2023-12-31",
        "geography": "Washington",
        "license": "Open Government Licence v3.0",
        "frequency": "Daily",
        "description": "Daily passenger boardings by ferry route and vessel.",
        "fields": [
            _f("route", "String", "Ferry route name"),
            _f("service_date", "Date", "Service date"),
            _f("passengers", "Integer", "Passenger count"),
            _f("vessel", "String", "Vessel name"),
        ],
        "resources": [
            _r("CSV", "18,432,000 bytes", "/beta/files/hde-002.csv"),
            _r("ZIP", "4.2 MB", "/beta/files/hde-002.zip"),
        ],
    },
    {
        "id": "hde-003",
        "title": "Citywide Wi-Fi Session Logs",
        "category": "Technology",
        "publisher": "Harbor Digital Services",
        "temporal": "2022-06-01 to 2023-05-31",
        "geography": "Washington",
        "license": "CC BY 4.0",
        "frequency": "Hourly",
        "description": "Aggregated public Wi-Fi sessions without device identifiers.",
        "fields": [
            _f("hotspot_id", "String", "Hotspot identifier"),
            _f("session_start", "Timestamp", "Session start time"),
            _f("duration_minutes", "Integer", "Session duration in minutes"),
            _f("bytes_transferred", "Integer", "Uplink plus downlink bytes"),
        ],
        "resources": [_r("CSV", "92.3 MB", "/beta/files/hde-003.csv")],
    },
    {
        "id": "hde-004",
        "title": "Road Surface Conditions 2023",
        "category": "Infrastructure",
        "publisher": "Harbor Public Works",
        "temporal": "2023-03-01 to 2023-03-31",
        "geography": "Washington",
        "license": "CC BY 4.0",
        "frequency": "As needed",
        "description": "Inspector condition scores for sampled road segments.",
        "fields": [
            _f("segment_id", "String", "Road segment identifier"),
            _f("condition_score", "Float", "Condition score from 0 to 100"),
            _f("inspected_on", "Date", "Inspection date"),
        ],
        "resources": [_r("CSV", "large", "/beta/files/hde-004.csv")],
    },
    {
        "id": "hde-005",
        "title": "Zoning Permits 2021-2023",
        "category": "Planning & Zoning",
        "publisher": "Harbor Planning Office",
        "temporal": "2021-01-01 to 2023-12-31",
        "geography": "Washington",
        "license": "",
        "frequency": "Weekly",
        "description": "Permit applications and decisions for the three-year window.",
        "fields": [
            _f("permit_id", "String", "Permit identifier"),
            _f("issued_date", "Date", "Decision date"),
            _f("zone_code", "String", "Zoning district code"),
            _f("valuation", "Float", "Declared project valuation"),
        ],
        "resources": [_r("PDF", "1.1 MB", "/beta/files/hde-005.pdf")],
    },
]

from .mock_gamma import GAMMA_DATASETS

DATASETS = {
    "alpha": ALPHA_DATASETS,
    "beta": BETA_DATASETS,
    "gamma": GAMMA_DATASETS,
}

PAGE_SIZE = {"alpha": 3, "beta": 4, "gamma": 3}
