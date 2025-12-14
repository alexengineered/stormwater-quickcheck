"""
Stormwater Quick-Check Calculator
Instant Runoff Estimator using the Rational Method (Q = CiA)

Optimized for Seattle/King County with embedded rainfall data from:
- City of Seattle Stormwater Manual (July 2021), Appendix F, Table F.18
- King County Surface Water Design Manual (2021, Amended 2024)
"""

import streamlit as st
import requests
from dataclasses import dataclass
from typing import Optional, Tuple, List

# =============================================================================
# CONSTANTS
# =============================================================================

SQFT_PER_ACRE = 43560
GPM_PER_CFS = 448.831  # gallons per minute per cubic foot per second

# Rational Method applicability limits
# Per Seattle Stormwater Manual Appendix F and King County SWDM Section 3.2.1
MAX_AREA_ACRES_STRICT = 10  # King County/Seattle limit for Rational Method
MAX_AREA_ACRES_EXTENDED = 50  # Extended limit with strong warnings

# King County approximate bounding box (for rainfall data applicability)
KING_COUNTY_BOUNDS = {
    "lat_min": 47.0,
    "lat_max": 47.8,
    "lon_min": -122.6,
    "lon_max": -121.5,
}

# =============================================================================
# RAINFALL DATA - Seattle Stormwater Manual (2021), Appendix F, Table F.18
# =============================================================================
# Source: City of Seattle Stormwater Manual (July 2021)
#         Directors' Rule 10-2021/DWW-200
#         Appendix F: Hydrologic Analysis and Design
#         Table F.18: Intensity-Duration-Frequency Values for the City of Seattle
#
# Data derived from City's 17-gauge precipitation measurement network
# and NOAA cooperative gauge network.
#
# Note: Table F.18 uses MINUTES for duration. We store as minutes internally.
# =============================================================================

SEATTLE_RAINFALL_DATA = {
    # Format: (return_period_years, duration_minutes): intensity_in_per_hr
    # 2-year storm
    (2, 5): 1.60, (2, 10): 1.10, (2, 15): 0.88, (2, 30): 0.61, (2, 60): 0.42,
    (2, 120): 0.29, (2, 180): 0.23,
    # 5-year storm
    (5, 5): 2.08, (5, 10): 1.40, (5, 15): 1.12, (5, 30): 0.76, (5, 60): 0.51,
    (5, 120): 0.35, (5, 180): 0.27,
    # 10-year storm
    (10, 5): 2.45, (10, 10): 1.64, (10, 15): 1.30, (10, 30): 0.87, (10, 60): 0.58,
    (10, 120): 0.39, (10, 180): 0.31,
    # 25-year storm
    (25, 5): 3.08, (25, 10): 2.03, (25, 15): 1.60, (25, 30): 1.05, (25, 60): 0.70,
    (25, 120): 0.46, (25, 180): 0.36,
    # 50-year storm
    (50, 5): 3.61, (50, 10): 2.36, (50, 15): 1.84, (50, 30): 1.21, (50, 60): 0.79,
    (50, 120): 0.52, (50, 180): 0.40,
    # 100-year storm
    (100, 5): 4.20, (100, 10): 2.72, (100, 15): 2.11, (100, 30): 1.37, (100, 60): 0.89,
    (100, 120): 0.57, (100, 180): 0.45,
}

# =============================================================================
# RUNOFF COEFFICIENTS - Seattle Stormwater Manual (2021), Table F.19
# Also consistent with King County SWDM (2021) Table 3.2.1.A
# =============================================================================

RUNOFF_COEFFICIENTS = {
    "Pavement and Roofs": {"C": 0.90, "description": "Streets, parking lots, driveways, rooftops"},
    "Gravel Areas": {"C": 0.80, "description": "Unpaved roads, gravel parking areas"},
    "Bare Soil": {"C": 0.60, "description": "Exposed earth, construction sites"},
    "Lawns": {"C": 0.25, "description": "Maintained grass areas"},
    "Landscaped Areas": {"C": 0.20, "description": "Gardens, planted beds (similar to Pasture)"},
    "Pasture": {"C": 0.20, "description": "Pasture land, agricultural grass"},
    "Light Forest": {"C": 0.15, "description": "Sparse tree cover, shrubs"},
    "Dense Forest": {"C": 0.10, "description": "Undisturbed natural forest areas"},
    "Open Water": {"C": 1.00, "description": "Ponds, lakes, and wetlands"},
}

RETURN_PERIODS = [2, 5, 10, 25, 50, 100]
DURATIONS_MINUTES = [5, 10, 15, 30, 60, 120, 180]
DURATION_LABELS = {
    5: "5-min", 10: "10-min", 15: "15-min", 30: "30-min",
    60: "1-hour", 120: "2-hour", 180: "3-hour"
}


# =============================================================================
# TIME OF CONCENTRATION CALCULATION
# =============================================================================
# Reference: Seattle Stormwater Manual Appendix F, Section F-6
#            King County SWDM Section 3.2.1
# "The design storm duration shall equal the time of concentration"
#
# Method: FAA (Federal Aviation Administration) - suitable for small urban areas
# Tc = 1.8 * (1.1 - C) * L^0.5 / S^0.33
# Where:
#   Tc = Time of concentration (minutes)
#   C = Runoff coefficient
#   L = Flow path length (feet)
#   S = Average slope (percent)
# =============================================================================

def calculate_tc_faa(runoff_c: float, flow_length_ft: float, slope_percent: float) -> float:
    """
    Calculate Time of Concentration using FAA method.

    Suitable for small urban drainage areas (< 10 acres).
    Reference: FAA AC 150/5320-5D, King County SWDM Section 3.2.1

    Args:
        runoff_c: Weighted runoff coefficient (0-1)
        flow_length_ft: Longest flow path length in feet
        slope_percent: Average slope along flow path (%)

    Returns:
        Time of concentration in minutes (minimum 5 minutes)
    """
    if flow_length_ft <= 0 or slope_percent <= 0:
        return 5.0  # Minimum Tc

    # FAA formula: Tc = 1.8 * (1.1 - C) * L^0.5 / S^0.33
    tc = 1.8 * (1.1 - runoff_c) * (flow_length_ft ** 0.5) / (slope_percent ** 0.333)

    # Enforce reasonable bounds (5 min minimum, 180 min maximum for this tool)
    return max(5.0, min(tc, 180.0))


def get_recommended_duration(tc_minutes: float) -> int:
    """
    Get the recommended storm duration based on calculated Tc.
    Rounds up to the nearest available duration in DURATIONS_MINUTES.
    """
    for duration in DURATIONS_MINUTES:
        if duration >= tc_minutes:
            return duration
    return DURATIONS_MINUTES[-1]  # Return max if Tc exceeds all options


# =============================================================================
# GEOCODING
# =============================================================================

@st.cache_data(ttl=3600, show_spinner=False)  # Cache for 1 hour
def geocode_address(address: str) -> Optional[Tuple[float, float, str]]:
    """
    Convert address to coordinates using Nominatim (OpenStreetMap).
    Returns (latitude, longitude, display_name) or None if failed.
    """
    if not address or not address.strip():
        return None

    try:
        url = "https://nominatim.openstreetmap.org/search"
        params = {
            "q": address.strip(),
            "format": "json",
            "limit": 1,
            "countrycodes": "us"
        }
        headers = {"User-Agent": "StormwaterQuickCheck/1.0 (civil-engineering-tool)"}
        response = requests.get(url, params=params, headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json()

        if data and len(data) > 0:
            return (
                float(data[0]["lat"]),
                float(data[0]["lon"]),
                data[0].get("display_name", address)
            )
        return None
    except requests.exceptions.Timeout:
        return None
    except requests.exceptions.RequestException:
        return None
    except (KeyError, ValueError, IndexError):
        return None


# =============================================================================
# RAINFALL DATA FUNCTIONS
# =============================================================================

def is_in_king_county(lat: float, lon: float) -> bool:
    """Check if coordinates are within King County bounds."""
    return (
            KING_COUNTY_BOUNDS["lat_min"] <= lat <= KING_COUNTY_BOUNDS["lat_max"] and
            KING_COUNTY_BOUNDS["lon_min"] <= lon <= KING_COUNTY_BOUNDS["lon_max"]
    )


def get_rainfall_intensity(
        lat: float,
        lon: float,
        return_period: int,
        duration_minutes: int
) -> Tuple[float, str, bool]:
    """
    Get rainfall intensity for given parameters.

    Returns:
        (intensity_in_per_hr, data_source_citation, is_local_data)

    Data Source: Seattle Stormwater Manual (2021), Table F.18
    """
    key = (return_period, duration_minutes)
    in_king_county = is_in_king_county(lat, lon)

    if key not in SEATTLE_RAINFALL_DATA:
        return 0.0, "Data not available for selected parameters", False

    intensity = SEATTLE_RAINFALL_DATA[key]

    if in_king_county:
        citation = (
            f"Seattle Stormwater Manual (July 2021), Appendix F, Table F.18 | "
            f"Lat: {lat:.4f}N, Lon: {abs(lon):.4f}W"
        )
        return intensity, citation, True
    else:
        citation = (
            f"Seattle Stormwater Manual (July 2021), Table F.18 (Seattle values). "
            f"WARNING: Location ({lat:.4f}N, {abs(lon):.4f}W) is outside King County. "
            f"Verify rainfall data for your specific jurisdiction."
        )
        return intensity, citation, False


# =============================================================================
# RATIONAL METHOD CALCULATION
# =============================================================================

@dataclass
class SurfaceArea:
    """Represents a surface type and its area."""
    surface_type: str
    area_sqft: float
    coefficient: float


@dataclass
class CalculationResult:
    """Complete calculation results with all parameters."""
    peak_runoff_cfs: float
    weighted_c: float
    total_area_sqft: float
    total_area_acres: float
    rainfall_intensity: float
    surfaces: List[SurfaceArea]
    return_period: int
    duration_minutes: int
    citation: str
    location: str
    coordinates: Tuple[float, float]
    warnings: List[str]
    tc_minutes: Optional[float] = None  # Time of concentration if calculated
    tc_flow_length: Optional[float] = None  # Flow path length used for Tc
    tc_slope: Optional[float] = None  # Slope used for Tc


def calculate_weighted_c(surfaces: List[SurfaceArea]) -> float:
    """Calculate area-weighted runoff coefficient."""
    if not surfaces:
        return 0.0

    total_area = sum(s.area_sqft for s in surfaces)
    if total_area == 0:
        return 0.0

    weighted_sum = sum(s.area_sqft * s.coefficient for s in surfaces)
    return weighted_sum / total_area


def calculate_rational_method(
        surfaces: List[SurfaceArea],
        rainfall_intensity: float,
        citation: str,
        return_period: int,
        duration_minutes: int,
        location: str,
        coordinates: Tuple[float, float],
        is_local_data: bool = True,
        tc_minutes: Optional[float] = None,
        tc_flow_length: Optional[float] = None,
        tc_slope: Optional[float] = None
) -> CalculationResult:
    """
    Calculate peak runoff using the Rational Method: Q = CiA

    Where:
        Q = Peak runoff (cfs)
        C = Runoff coefficient (dimensionless)
        i = Rainfall intensity (in/hr)
        A = Drainage area (acres)

    Reference: Seattle Stormwater Manual (2021), Appendix F, Section F-6
               King County SWDM (2021), Section 3.2.1
    """
    warnings = []

    # Calculate totals
    total_area_sqft = sum(s.area_sqft for s in surfaces)
    total_area_acres = total_area_sqft / SQFT_PER_ACRE
    weighted_c = calculate_weighted_c(surfaces)

    # Rational Method: Q = CiA
    peak_runoff_cfs = weighted_c * rainfall_intensity * total_area_acres

    # Add warnings for edge cases
    if total_area_acres > MAX_AREA_ACRES_EXTENDED:
        warnings.append(
            f"âš ï¸ Area ({total_area_acres:.1f} acres) exceeds {MAX_AREA_ACRES_EXTENDED} acres. "
            f"Rational Method not appropriate. Use continuous simulation (WWHM) per King County/Seattle requirements."
        )
    elif total_area_acres > MAX_AREA_ACRES_STRICT:
        warnings.append(
            f"âš ï¸ Area ({total_area_acres:.1f} acres) exceeds {MAX_AREA_ACRES_STRICT} acres. "
            f"King County/Seattle limit Rational Method to <10 acres for conveyance sizing. "
            f"Consider continuous simulation (WWHM) for permit applications."
        )

    if weighted_c > 0.95:
        warnings.append("âš ï¸ Very high runoff coefficient (C > 0.95). Verify surface types are correct.")

    if not is_local_data:
        warnings.append("âš ï¸ Using Seattle default rainfall data. Verify intensity for your specific jurisdiction.")

    return CalculationResult(
        peak_runoff_cfs=peak_runoff_cfs,
        weighted_c=weighted_c,
        total_area_sqft=total_area_sqft,
        total_area_acres=total_area_acres,
        rainfall_intensity=rainfall_intensity,
        surfaces=surfaces,
        return_period=return_period,
        duration_minutes=duration_minutes,
        citation=citation,
        location=location,
        coordinates=coordinates,
        warnings=warnings,
        tc_minutes=tc_minutes,
        tc_flow_length=tc_flow_length,
        tc_slope=tc_slope
    )


# OUTPUT FORMATTING
# =============================================================================

def format_report(result: CalculationResult) -> str:
    """Generate formatted report text for copy/paste into engineering reports."""

    lat = result.coordinates[0]
    lon = result.coordinates[1]
    lon_display = abs(lon)
    duration_label = DURATION_LABELS.get(result.duration_minutes, f"{result.duration_minutes}-min")

    lines = [
        "=" * 70,
        "STORMWATER RUNOFF CALCULATION - RATIONAL METHOD",
        "=" * 70,
        "",
        "PROJECT LOCATION",
        "-" * 40,
        f"Address/Location: {result.location}",
        f"Coordinates: {lat:.6f}N, {lon_display:.6f}W",
        "",
        "DESIGN STORM PARAMETERS",
        "-" * 40,
        f"Return Period: {result.return_period}-year storm",
        f"Duration: {duration_label}",
        f"Rainfall Intensity (i): {result.rainfall_intensity:.2f} in/hr",
    ]

    # Add Time of Concentration if calculated
    if result.tc_minutes is not None:
        lines.extend([
            "",
            "TIME OF CONCENTRATION (Tc)",
            "-" * 40,
            f"Calculated Tc: {result.tc_minutes:.1f} minutes",
            f"Flow Path Length: {result.tc_flow_length:.0f} ft",
            f"Average Slope: {result.tc_slope:.1f}%",
            "Method: FAA (Federal Aviation Administration)",
            "Note: Preliminary estimate - verify with site-specific analysis",
        ])

    lines.extend([
        "",
        "SITE CHARACTERISTICS",
        "-" * 40,
    ])

    for surface in result.surfaces:
        pct = (surface.area_sqft / result.total_area_sqft) * 100 if result.total_area_sqft > 0 else 0
        lines.append(f"  {surface.surface_type}:")
        lines.append(f"    Area: {surface.area_sqft:,.0f} sq ft ({pct:.1f}%)")
        lines.append(f"    Runoff Coefficient (C): {surface.coefficient:.2f}")

    lines.extend([
        "",
        f"Total Drainage Area: {result.total_area_sqft:,.0f} sq ft ({result.total_area_acres:.3f} acres)",
        f"Weighted Runoff Coefficient (C): {result.weighted_c:.3f}",
        "",
        "CALCULATION",
        "-" * 40,
        "Rational Method Formula: Q = C  i  A",
        "",
        f"  Q = {result.weighted_c:.3f}  {result.rainfall_intensity:.2f} in/hr  {result.total_area_acres:.3f} acres",
        f"  Q = {result.peak_runoff_cfs:.3f} cfs",
        "",
        "RESULT",
        "-" * 40,
        f"Peak Runoff (Q): {result.peak_runoff_cfs:.2f} cfs ({result.peak_runoff_cfs * GPM_PER_CFS:.1f} gpm)",
        "",
    ])

    if result.warnings:
        lines.extend([
            "WARNINGS",
            "-" * 40,
        ])
        for warning in result.warnings:
            lines.append(f"  {warning}")
        lines.append("")

    lines.extend([
        "REFERENCES",
        "-" * 40,
        "Rainfall Intensity Data:",
        "  City of Seattle Stormwater Manual (July 2021)",
        "  Appendix F, Table F.18: Intensity-Duration-Frequency Values",
        "  Directors' Rule 10-2021/DWW-200",
        "",
        "Runoff Coefficients:",
        "  Seattle Stormwater Manual (2021), Table F.19",
        "  King County Surface Water Design Manual (2021, Amended 2024)",
        "  Section 3.2.1, Table 3.2.1.A",
        "",
        "Methodology:",
        "  Rational Method per Seattle SWM Appendix F, Section F-6",
        "  King County SWDM Section 3.2.1 (for areas < 10 acres)",
        "",
        "Note: Both Seattle and King County require continuous simulation",
        "(WWHM/MGSFlood) for most permit applications. This tool provides",
        "preliminary estimates for conveyance sizing only.",
        "",
        "DISCLAIMER",
        "-" * 40,
        "This calculation is for preliminary planning purposes only.",
        "All values should be verified by a licensed professional engineer.",
        "Consult local jurisdiction requirements for design standards.",
        "",
        "=" * 70,
    ])

    return "\n".join(lines)


def generate_pdf_report(result: CalculationResult) -> bytes:
    """Generate a professional PDF report using fpdf2."""
    from fpdf import FPDF
    from io import BytesIO

    pdf = FPDF()
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=15)

    # Title
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, "STORMWATER RUNOFF CALCULATION", ln=True, align="C")
    pdf.set_font("Helvetica", "", 11)
    pdf.cell(0, 6, "Rational Method (Q = CiA)", ln=True, align="C")
    pdf.ln(8)

    # Location
    lat, lon = result.coordinates
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 8, "PROJECT LOCATION", ln=True)
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(0, 6, f"Address: {result.location}", ln=True)
    pdf.cell(0, 6, f"Coordinates: {lat:.6f} N, {abs(lon):.6f} W", ln=True)
    pdf.ln(4)

    # Design Storm
    duration_label = DURATION_LABELS.get(result.duration_minutes, f"{result.duration_minutes}-min")
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 8, "DESIGN STORM PARAMETERS", ln=True)
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(0, 6, f"Return Period: {result.return_period}-year storm", ln=True)
    pdf.cell(0, 6, f"Duration: {duration_label}", ln=True)
    pdf.cell(0, 6, f"Rainfall Intensity: {result.rainfall_intensity:.2f} in/hr", ln=True)
    pdf.ln(4)

    # Time of Concentration (if calculated)
    if result.tc_minutes is not None:
        pdf.set_font("Helvetica", "B", 12)
        pdf.cell(0, 8, "TIME OF CONCENTRATION", ln=True)
        pdf.set_font("Helvetica", "", 10)
        pdf.cell(0, 6, f"Calculated Tc: {result.tc_minutes:.1f} minutes", ln=True)
        pdf.cell(0, 6, f"Flow Path Length: {result.tc_flow_length:.0f} ft", ln=True)
        pdf.cell(0, 6, f"Average Slope: {result.tc_slope:.1f}%", ln=True)
        pdf.cell(0, 6, "Method: FAA (preliminary estimate)", ln=True)
        pdf.ln(4)

    # Site Characteristics Table
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 8, "SITE CHARACTERISTICS", ln=True)
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_fill_color(10, 22, 43)
    pdf.set_text_color(255, 255, 255)
    pdf.cell(70, 7, "Surface Type", border=1, fill=True)
    pdf.cell(35, 7, "Area (sq ft)", border=1, align="R", fill=True)
    pdf.cell(25, 7, "% Total", border=1, align="R", fill=True)
    pdf.cell(25, 7, "C Value", border=1, align="R", fill=True)
    pdf.ln()

    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(0, 0, 0)
    for surface in result.surfaces:
        pct = (surface.area_sqft / result.total_area_sqft) * 100 if result.total_area_sqft > 0 else 0
        pdf.cell(70, 6, surface.surface_type, border=1)
        pdf.cell(35, 6, f"{surface.area_sqft:,.0f}", border=1, align="R")
        pdf.cell(25, 6, f"{pct:.1f}%", border=1, align="R")
        pdf.cell(25, 6, f"{surface.coefficient:.2f}", border=1, align="R")
        pdf.ln()

    # Total row
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_fill_color(230, 230, 230)
    pdf.cell(70, 6, "TOTAL", border=1, fill=True)
    pdf.cell(35, 6, f"{result.total_area_sqft:,.0f}", border=1, align="R", fill=True)
    pdf.cell(25, 6, "100%", border=1, align="R", fill=True)
    pdf.cell(25, 6, f"{result.weighted_c:.3f}", border=1, align="R", fill=True)
    pdf.ln(8)

    # Calculation
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 8, "CALCULATION", ln=True)
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(0, 6, "Rational Method Formula: Q = C x i x A", ln=True)
    pdf.cell(0, 6,
             f"Q = {result.weighted_c:.3f} x {result.rainfall_intensity:.2f} in/hr x {result.total_area_acres:.3f} acres",
             ln=True)
    pdf.ln(4)

    # Result
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 10,
             f"Peak Runoff (Q) = {result.peak_runoff_cfs:.2f} cfs ({result.peak_runoff_cfs * GPM_PER_CFS:.0f} gpm)",
             ln=True)
    pdf.ln(4)

    # Warnings
    if result.warnings:
        pdf.set_font("Helvetica", "B", 12)
        pdf.cell(0, 8, "WARNINGS", ln=True)
        pdf.set_font("Helvetica", "", 9)
        for warning in result.warnings:
            clean_warning = warning.replace("\u26a0\ufe0f", "WARNING:").encode('latin-1', 'replace').decode('latin-1')
            pdf.multi_cell(0, 5, f"- {clean_warning}")
        pdf.ln(4)

    # References
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 8, "REFERENCES", ln=True)
    pdf.set_font("Helvetica", "", 9)
    pdf.cell(0, 5, "- Seattle Stormwater Manual (July 2021), Appendix F, Table F.18", ln=True)
    pdf.cell(0, 5, "- King County Surface Water Design Manual (2021, Amended 2024)", ln=True)
    pdf.cell(0, 5, "- Rational Method per Seattle SWM Appendix F, Section F-6", ln=True)
    pdf.ln(8)

    # Disclaimer
    pdf.set_font("Helvetica", "I", 8)
    pdf.set_text_color(128, 128, 128)
    pdf.multi_cell(0, 4,
                   "DISCLAIMER: This calculation is for preliminary planning purposes only. "
                   "All values should be verified by a licensed professional engineer. "
                   "Consult local jurisdiction requirements for design standards."
                   )

    return bytes(pdf.output())


# =============================================================================
# STREAMLIT UI
# =============================================================================

def apply_custom_css():
    """Apply custom styling including rain animation."""
    st.markdown("""
    <style>
    /* Import Google Fonts */
    @import url('https://fonts.googleapis.com/css2?family=Exo+2:wght@300;400;600;700&family=Open+Sans:wght@400;600&display=swap');
    

    /* Global white text */
    h1, h2, h3, h4, h5, h6,
    .stApp, .stApp p, .stApp span, .stApp div, .stApp label,
    .stMarkdown, .stMarkdown p, .stMarkdown span,
    .stCaption, [data-testid="stMarkdownContainer"],
    [data-testid="stMetricValue"], [data-testid="stMetricLabel"] {
        color: #ffffff !important;
    }
    
    /* Input fields - dark text on white background */
    .stTextInput input, .stNumberInput input {
        color: #1a3a5c !important;
        background: rgba(255, 255, 255, 0.80) !important;
        border: 2px solid transparent !important;
        border-radius: 8px !important;
    }
    
    .stTextInput input:focus, .stNumberInput input:focus {
        border-color: #00d4ff !important;
        box-shadow: 0 0 15px rgba(0, 212, 255, 0.3) !important;
    }
    
    /* Selectbox - dark text on white background */
    .stSelectbox [data-baseweb="select"] span,
    .stSelectbox [data-baseweb="select"] div {
        color: #1a3a5c !important;
    }
    
    .stSelectbox > div > div {
        background: rgba(255, 255, 255, 0.95) !important;
        border-radius: 8px !important;
    }
    
    /* Expander - header styling */
    [data-testid="stExpander"] summary span {
        color: #ffffff !important;
        font-size: 1.1rem !important;
    }
    
    [data-testid="stExpander"] summary:hover {
        background: rgba(0, 212, 255, 0.3) !important;
    }
    
    [data-testid="stExpander"] details[open] summary {
        background: rgba(0, 188, 212, 0.4) !important;
    }
    
    /* Expander - content area */
    [data-testid="stExpander"] [data-testid="stExpanderDetails"] {
        background: rgba(6, 26, 55, 0.20) !important;
    }
    
    /* Code block - dark text on white background */
    .stCode, .stCode code, .stCode pre {
        color: #1a3a5c !important;
        background: #ffffff !important;
    }
    
    /* Download button - match expander content background */
    .stDownloadButton button {
        background: rgba(6, 26, 55, 0.20) !important;
        color: #ffffff !important;
    }
    
    /* Main container */
    .stApp {
        background: linear-gradient(to top, #0f2027, #203a43, #2c5364);
        font-family: 'Open Sans', sans-serif;
    }
    
    /* Header styling */
    .main-header {
        font-family: 'Exo 2', sans-serif;
        font-size: 2.5rem;
        font-weight: 700;
        color: #ffffff;
        text-align: center;
        margin-bottom: 0;
        text-shadow: 0 2px 10px rgba(0, 200, 255, 0.3);
    }
    
    .sub-header {
        font-family: 'Exo 2', sans-serif;
        font-size: 1rem;
        font-weight: 300;
        color: #7ec8e3;
        text-align: center;
        letter-spacing: 3px;
        text-transform: uppercase;
        margin-top: -10px;
        margin-bottom: 30px;
    }
    
    /* Card styling */
    .input-card {
        background: rgba(255, 255, 255, 0.08);
        border: 1px solid rgba(126, 200, 227, 0.2);
        border-radius: 12px;
        padding: 20px;
        margin-bottom: 20px;
        backdrop-filter: blur(10px);
    }
    
    /* Input labels */
    .stTextInput label, .stSelectbox label, .stNumberInput label {
        color: #b8d4e3 !important;
        font-weight: 600;
        font-size: 0.9rem;
        text-transform: uppercase;
        letter-spacing: 1px;
    }
    
    /* Buttons */
    .stButton > button {
        background: linear-gradient(180deg, #1c7585 0%, #104e57 100%) !important;
        color: #0a1628 !important;
        font-family: 'Exo 2', sans-serif !important;
        font-weight: 700 !important;
        font-size: 1.1rem !important;
        letter-spacing: 2px !important;
        text-transform: uppercase !important;
        border: none !important;
        border-radius: 8px !important;
        padding: 15px 40px !important;
        box-shadow: 0 2px 6px rgba(0, 212, 255, 0.4) !important;
        transition: all 0.3s ease !important;
    }
    
    .stButton > button:hover {
        transform: translateY(-2px) !important;
        box-shadow: 0 6px 25px rgba(0, 212, 255, 0.6) !important;
    }
    

    /* Results section */
    .results-card {
        background: rgba(0, 212, 255, 0.1);
        border: 2px solid #00d4ff;
        border-radius: 12px;
        padding: 25px;
        margin-top: 20px;
    }
    
    .result-value {
        font-family: 'Exo 2', sans-serif;
        font-size: 3rem;
        font-weight: 700;
        color: #00d4ff;
        text-align: center;
        text-shadow: 0 0 20px rgba(0, 212, 255, 0.5);
    }
    
    .result-label {
        font-size: 1rem;
        color: #7ec8e3;
        text-align: center;
        text-transform: uppercase;
        letter-spacing: 2px;
    }
    
    /* Metrics */
    .metric-container {
        background: rgba(255, 255, 255, 0.05);
        border-radius: 8px;
        padding: 15px;
        text-align: center;
    }
    
    /* Warning boxes */
    .stAlert {
        background: linear-gradient(180deg, #1c7585 0%, #104e57 100%) !important;
        border-color: #104e57 !important;
    }
    
    /* Success boxes */
    .element-container .stSuccess {
        background: rgba(0, 212, 255, 0.15) !important;
        border-color: #00d4ff !important;
    }
    
    /* Divider */
    hr {
        border-color: rgba(126, 200, 227, 0.3) !important;
    }
    
    /* Hide Streamlit branding */
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    
    h3 {
        padding: 1.75rem 0 1rem !important;
    }
    
    /* =========================================================================
       RAIN ANIMATION - CSS Only
       ========================================================================= */
    
    /* Rain container - fixed background layer */
    .rain-container {
        position: fixed;
        top: 30%;
        left: 0;
        width: 100%;
        height: 100%;
        pointer-events: none;
        z-index: 1;
        overflow: hidden;
        display: grid;
        grid-template-columns: repeat(3, 1fr);
        grid-template-rows: repeat(3, 1fr);
    }
    
    /* Individual rain drop unit */
    .rain {
        position: relative;
        width: 100%;
        height: 100%;
    }
    
    /* Timing variations for each rain drop */
    .rain:nth-of-type(1) {
        --duration: 2.7s;
        --delay: 1s;
        transform: translate(10%, 10%) scale(0.9);
    }
    
    .rain:nth-of-type(2) {
        --duration: 2.1s;
        --delay: 1.2s;
        transform: translate(-20%, 40%) scale(1.3);
    }
    
    .rain:nth-of-type(3) {
        --duration: 2.3s;
        --delay: 2s;
        transform: translate(0%, 50%) scale(1.1);
    }
    
    .rain:nth-of-type(4) {
        --duration: 2s;
        --delay: 4s;
        transform: translate(0%, -10%) scale(1.2);
    }
    
    .rain:nth-of-type(5) {
        --duration: 2.5s;
        --delay: 0s;
        transform: translate(10%, 0%) scale(0.9);
    }
    
    .rain:nth-of-type(6) {
        --duration: 2s;
        --delay: 2.7s;
        transform: translate(-20%, 0%) scale(1);
    }
    
    .rain:nth-of-type(7) {
        --duration: 1.8s;
        --delay: 1.3s;
        transform: translate(20%, -40%) scale(1.2);
    }
    
    .rain:nth-of-type(8) {
        --duration: 2.2s;
        --delay: 0s;
        transform: translate(20%, 0%) scale(1);
    }
    
    .rain:nth-of-type(9) {
        --duration: 2s;
        --delay: 0.5s;
        transform: translate(0%, -10%) scale(1.3);
    }
    
    /* Falling drop */
    .drop {
        background-color: rgba(255, 255, 255, 0.1);
        width: 2px;
        height: 30px;
        position: absolute;
        top: calc(50% - 30px);
        left: calc(50% - 1.5px);
        animation-name: fall;
        animation-duration: var(--duration);
        animation-delay: var(--delay);
        animation-iteration-count: infinite;
        animation-timing-function: ease-in;
        animation-fill-mode: backwards;
    }
    
    @keyframes fall {
        0% {
            transform: translateY(-40vh);
        }
        45% {
            transform: translateY(0%);
            opacity: 1;
        }
        46% {
            opacity: 0;
        }
        100% {
            opacity: 0;
        }
    }
    
    .waves {
    position: relative;
    width: 100%;
    aspect-ratio: 1 / 1; 
    max-width: 300px; 
}
    /* Ripple waves */
    .waves > div {
        position: absolute;
        top: 0;
        left: 0;
        right: 0;
        bottom: 0;
        border-radius: 50%;
        border: solid rgba(255, 255, 255, 0.17) 7px;
        animation-name: spread;
        animation-duration: var(--duration);
        animation-delay: var(--delay);
        animation-iteration-count: infinite;
        animation-timing-function: ease-out;
        animation-fill-mode: backwards;
    }
    
    .waves > div:nth-child(2) {
        animation-delay: calc(var(--delay) + 0.3s);
        animation-timing-function: linear;
    }
    
    @keyframes spread {
        0% {
            transform: scale(0);
            opacity: 1;
        }
        40% {
            transform: scale(0);
            opacity: 1;
        }
        100% {
            transform: scale(.3);
            opacity: 0;
        }
    }
    
    /* Splash particles */
    .particles > div {
        position: absolute;
        border-radius: 100%;
        background-color: rgba(255, 255, 255, 0.17);
        animation-duration: var(--duration);
        animation-delay: var(--delay);
        animation-iteration-count: infinite;
        animation-timing-function: ease;
        animation-fill-mode: backwards;
    }
    
    .particles > div:nth-child(1) {
        width: 7px;
        height: 7px;
        top: 50%;
        left: 50%;
        animation-name: jumpLeft;
    }
    
    .particles > div:nth-child(2) {
        width: 5px;
        height: 5px;
        top: 30%;
        left: 50%;
        animation-name: jumpLeft;
        animation-delay: calc(var(--delay) + 0.1s);
    }
    
    .particles > div:nth-child(3) {
        width: 3px;
        height: 3px;
        top: 20%;
        left: 70%;
        animation-name: jumpRight;
        animation-delay: calc(var(--delay) + 0.15s);
    }
    
    .particles > div:nth-child(4) {
        width: 5px;
        height: 5px;
        top: 30%;
        left: 50%;
        animation-name: jumpRight;
        animation-delay: calc(var(--delay) + 0.3s);
    }
    
    @keyframes jumpLeft {
        0% {
            transform: translate(0, 0) scale(0);
        }
        45% {
            transform: translate(0, 0) scale(0);
        }
        60% {
            transform: translate(-50px, -90px) scale(1);
        }
        100% {
            transform: translate(-60px, 0px) scale(0.1);
        }
    }
    
    @keyframes jumpRight {
        0% {
            transform: translate(0, 0) scale(0);
        }
        45% {
            transform: translate(0, 0) scale(0);
        }
        60% {
            transform: translate(30px, -80px) scale(1);
        }
        100% {
            transform: translate(50px, 0px) scale(0.1);
        }
    }
    
    a {
  text-decoration: none !important;
}
    </style>
    """, unsafe_allow_html=True)


def inject_rain_animation():
    """Inject the rain animation HTML elements."""
    rain_html = """
    <div class="rain-container">
        <div class="rain">
            <div class="drop"></div>
            <div class="waves"><div></div><div></div></div>
            <div class="particles"><div></div><div></div><div></div><div></div></div>
        </div>
        <div class="rain">
            <div class="drop"></div>
            <div class="waves"><div></div><div></div></div>
            <div class="particles"><div></div><div></div><div></div><div></div></div>
        </div>
        <div class="rain">
            <div class="drop"></div>
            <div class="waves"><div></div><div></div></div>
            <div class="particles"><div></div><div></div><div></div><div></div></div>
        </div>
        <div class="rain">
            <div class="drop"></div>
            <div class="waves"><div></div><div></div></div>
            <div class="particles"><div></div><div></div><div></div><div></div></div>
        </div>
        <div class="rain">
            <div class="drop"></div>
            <div class="waves"><div></div><div></div></div>
            <div class="particles"><div></div><div></div><div></div><div></div></div>
        </div>
        <div class="rain">
            <div class="drop"></div>
            <div class="waves"><div></div><div></div></div>
            <div class="particles"><div></div><div></div><div></div><div></div></div>
        </div>
        <div class="rain">
            <div class="drop"></div>
            <div class="waves"><div></div><div></div></div>
            <div class="particles"><div></div><div></div><div></div><div></div></div>
        </div>
        <div class="rain">
            <div class="drop"></div>
            <div class="waves"><div></div><div></div></div>
            <div class="particles"><div></div><div></div><div></div><div></div></div>
        </div>
        <div class="rain">
            <div class="drop"></div>
            <div class="waves"><div></div><div></div></div>
            <div class="particles"><div></div><div></div><div></div><div></div></div>
        </div>
    </div>
    """
    st.markdown(rain_html, unsafe_allow_html=True)


def main():
    st.set_page_config(
        page_title="Stormwater Quick-Check",
        page_icon="",
        layout="centered",
        initial_sidebar_state="collapsed"
    )

    apply_custom_css()
    inject_rain_animation()

    # Header
    st.markdown("""
    <h1 class="main-header">STORMWATER QUICK-CHECK</h1>
    <p class="sub-header">Instant Runoff Estimator</p>
    """, unsafe_allow_html=True)

    # Initialize session state
    if 'surfaces' not in st.session_state:
        st.session_state.surfaces = [{"type": "Pavement and Roofs", "area": 10000.0}]
    if 'result' not in st.session_state:
        st.session_state.result = None
    if 'address_input' not in st.session_state:
        st.session_state.address_input = ""
    if 'use_tc_calculator' not in st.session_state:
        st.session_state.use_tc_calculator = False
    if 'flow_length' not in st.session_state:
        st.session_state.flow_length = 200.0
    if 'slope_percent' not in st.session_state:
        st.session_state.slope_percent = 2.0

    # Location Input
    st.markdown("### Project Location")

    address = st.text_input(
        "Project Address",
        value=st.session_state.address_input,
        placeholder="Type an address and press Enter",
        help="Enter a street address within King County/Seattle",
        key="address_field"
    )

    # Update session state
    if address != st.session_state.address_input:
        st.session_state.address_input = address

    # Geocoding and coordinate handling
    lat, lon, location_name = 47.6062, -122.3321, "Seattle, WA (default)"

    if address and address.strip():
        geocode_result = geocode_address(address)
        if geocode_result:
            lat, lon, location_name = geocode_result
            # Check if in King County
            if is_in_king_county(lat, lon):
                st.info(f" {location_name[:70]}...")
            else:
                st.warning(f" {location_name[:50]}... (outside King County - using Seattle rainfall data)")
        else:
            st.warning("Could not geocode address. Using Seattle, WA default coordinates.")
    else:
        st.info("Seattle, WA is used by default. Enter an address to use your project location.")

    # Time of Concentration Calculator (Optional)
    st.markdown("### Time of Concentration (Optional)")

    use_tc = st.checkbox(
        "Calculate Tc to determine storm duration",
        value=st.session_state.use_tc_calculator,
        help="Use FAA method to estimate Time of Concentration. Per Seattle SWM: 'Design storm duration shall equal the time of concentration.'"
    )
    st.session_state.use_tc_calculator = use_tc

    calculated_tc = None
    recommended_duration = None

    if use_tc:
        st.caption("Preliminary Tc Estimate (verify with site-specific analysis)")

        col1, col2 = st.columns(2)
        with col1:
            flow_length = st.number_input(
                "Flow Path Length (ft)",
                min_value=10.0,
                max_value=5000.0,
                value=st.session_state.flow_length,
                step=10.0,
                help="Longest distance water travels from watershed boundary to outlet"
            )
            st.session_state.flow_length = flow_length

        with col2:
            slope_percent = st.number_input(
                "Average Slope (%)",
                min_value=0.1,
                max_value=50.0,
                value=st.session_state.slope_percent,
                step=0.5,
                help="Average slope along the flow path"
            )
            st.session_state.slope_percent = slope_percent

        # Use a default C for Tc calculation (will be refined after surfaces are entered)
        # Using 0.5 as reasonable urban default; actual weighted C shown in results
        default_c_for_tc = 0.5
        calculated_tc = calculate_tc_faa(default_c_for_tc, flow_length, slope_percent)
        recommended_duration = get_recommended_duration(calculated_tc)

        st.info(
            f"**Estimated Tc:** {calculated_tc:.1f} min Recommended Duration:** {DURATION_LABELS.get(recommended_duration, f'{recommended_duration}-min')}")
        st.caption(
            "Tc calculated using FAA method with C=0.5 (urban default). Final calculation uses your actual weighted C.")

    st.markdown("###  Design Storm")

    col1, col2 = st.columns(2)
    with col1:
        return_period = st.selectbox(
            "Return Period",
            options=RETURN_PERIODS,
            index=2,  # Default to 10-year
            format_func=lambda x: f"{x}-year storm",
            help="Frequency of design storm event"
        )
    with col2:
        # Set default duration index based on Tc calculation if enabled
        if recommended_duration is not None:
            default_duration_index = DURATIONS_MINUTES.index(recommended_duration)
        else:
            default_duration_index = 4  # Default to 60 minutes (1-hour)

        duration_minutes = st.selectbox(
            "Storm Duration",
            options=DURATIONS_MINUTES,
            index=default_duration_index,
            format_func=lambda x: DURATION_LABELS.get(x, f"{x}-min"),
            help="Duration for intensity calculation (should equal Time of Concentration)"
        )

    # Get rainfall intensity
    rainfall_intensity, citation, is_local_data = get_rainfall_intensity(lat, lon, return_period, duration_minutes)

    # Show rainfall intensity
    duration_label = DURATION_LABELS.get(duration_minutes, f"{duration_minutes}-min")
    if is_local_data:
        st.info(
            f"**Rainfall Intensity:** {rainfall_intensity:.2f} in/hr for {return_period}-year, {duration_label} storm")
    else:
        st.warning(
            f"**Rainfall Intensity:** {rainfall_intensity:.2f} in/hr (Seattle default - verify for your location)")

    # Surface Areas
    st.markdown("###  Site Surfaces")
    st.caption("Add one or more surface types with their areas")

    # Surface input controls
    num_surfaces = st.number_input(
        "Number of surface types",
        min_value=1,
        max_value=10,
        value=len(st.session_state.surfaces),
        key="num_surfaces"
    )

    # Adjust surface list length
    current_len = len(st.session_state.surfaces)
    if num_surfaces > current_len:
        for _ in range(num_surfaces - current_len):
            st.session_state.surfaces.append({"type": "Lawns", "area": 5000.0})
    elif num_surfaces < current_len:
        st.session_state.surfaces = st.session_state.surfaces[:num_surfaces]

    surfaces = []
    total_area = 0.0
    surface_type_list = list(RUNOFF_COEFFICIENTS.keys())

    for i in range(num_surfaces):
        col1, col2 = st.columns([2, 1])

        current_type = st.session_state.surfaces[i].get("type", surface_type_list[0])
        current_area = st.session_state.surfaces[i].get("area", 0.0)

        if current_type not in surface_type_list:
            current_type = surface_type_list[0]

        with col1:
            surface_type = st.selectbox(
                f"Surface Type {i + 1}",
                options=surface_type_list,
                index=surface_type_list.index(current_type),
                key=f"surface_type_{i}",
                help=RUNOFF_COEFFICIENTS[current_type]["description"]
            )

        with col2:
            area = st.number_input(
                f"Area (sq ft) {i + 1}",
                min_value=0.0,
                max_value=50000000.0,
                value=float(current_area),
                step=100.0,
                key=f"area_{i}"
            )

        st.session_state.surfaces[i] = {"type": surface_type, "area": area}

        if area > 0:
            c_value = RUNOFF_COEFFICIENTS[surface_type]["C"]
            surfaces.append(SurfaceArea(surface_type, area, c_value))
            total_area += area
            st.caption(f"   C = {c_value} | {RUNOFF_COEFFICIENTS[surface_type]['description']}")

    # Show summary
    if total_area > 0:
        st.markdown("---")
        cols = st.columns(3)
        total_acres = total_area / SQFT_PER_ACRE
        with cols[0]:
            st.metric("Total Area", f"{total_area:,.0f} sq ft")
        with cols[1]:
            st.metric("Total Area", f"{total_acres:.3f} acres")
        with cols[2]:
            weighted_c = calculate_weighted_c(surfaces)
            st.metric("Weighted C", f"{weighted_c:.3f}")

        # Area warnings
        if total_acres > MAX_AREA_ACRES_EXTENDED:
            st.error(
                f"WARNING: Total area ({total_acres:.1f} acres) exceeds Rational Method limit of {MAX_AREA_ACRES_EXTENDED} acres")
        elif total_acres > MAX_AREA_ACRES_STRICT:
            st.warning(
                f"WARNING: Area exceeds {MAX_AREA_ACRES_STRICT} acres. King County/Seattle limit Rational Method to <10 acres.")

    # Calculate Button
    st.markdown("---")

    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        calculate_disabled = len(surfaces) == 0 or total_area == 0
        calculate_button = st.button(
            " CALCULATE RUNOFF",
            use_container_width=True,
            disabled=calculate_disabled
        )

    # Perform calculation
    if calculate_button and surfaces:
        result = calculate_rational_method(
            surfaces=surfaces,
            rainfall_intensity=rainfall_intensity,
            citation=citation,
            return_period=return_period,
            duration_minutes=duration_minutes,
            location=location_name,
            coordinates=(lat, lon),
            is_local_data=is_local_data,
            tc_minutes=calculated_tc,
            tc_flow_length=st.session_state.flow_length if use_tc else None,
            tc_slope=st.session_state.slope_percent if use_tc else None
        )
        st.session_state.result = result

    # Display Results
    if st.session_state.result:
        result = st.session_state.result

        st.markdown("---")
        st.markdown("## Results")

        # Main result display
        st.markdown(f"""
        <div class="results-card">
            <p class="result-label">Peak Runoff (Q)</p>
            <p class="result-value">{result.peak_runoff_cfs:.2f} cfs</p>
            <p class="result-label">{result.peak_runoff_cfs * GPM_PER_CFS:.0f} gallons per minute</p>
        </div>
        """, unsafe_allow_html=True)

        # Warnings
        for warning in result.warnings:
            st.warning(warning)

        # Calculation breakdown
        with st.expander("Calculation Details", expanded=True):
            st.markdown("**Rational Method Formula:** Q = C  i  A")
            duration_label = DURATION_LABELS.get(result.duration_minutes, f"{result.duration_minutes}-min")
            st.markdown(f"""
            - **C** (Weighted Runoff Coefficient) = {result.weighted_c:.3f}
            - **i** (Rainfall Intensity) = {result.rainfall_intensity:.2f} in/hr ({result.return_period}-yr, {duration_label})
            - **A** (Drainage Area) = {result.total_area_acres:.3f} acres
            
            **Q** = {result.weighted_c:.3f}  {result.rainfall_intensity:.2f}  {result.total_area_acres:.3f} = **{result.peak_runoff_cfs:.3f} cfs**
            """)

        # Citation
        with st.expander("Data Sources & Citations"):
            st.markdown(f"""
            **Rainfall Intensity Data:**
            City of Seattle Stormwater Manual (July 2021)
            Appendix F, Table F.18: Intensity-Duration-Frequency Values
            Directors' Rule 10-2021/DWW-200
            
            **Runoff Coefficients:**
            Seattle Stormwater Manual (2021), Table F.19
            King County Surface Water Design Manual (2021, Amended 2024)
            Section 3.2.1, Table 3.2.1.A
            
            **Methodology:**
            Rational Method per Seattle SWM Appendix F, Section F-6
            King County SWDM Section 3.2.1 (for areas < 10 acres)
            
            **Important Note:**
            Both Seattle and King County require continuous simulation modeling
            (WWHM or MGSFlood) for most permit applications. This tool provides
            preliminary estimates for conveyance sizing only.
            """)

        # Copy-ready report
        with st.expander("Copy-Ready Report"):
            report_text = format_report(result)
            st.code(report_text, language=None)
            col1, col2 = st.columns(2)
            with col1:
                st.download_button(
                    label="Download (.txt)",
                    data=report_text,
                    file_name="stormwater_calculation.txt",
                    mime="text/plain"
                )
            with col2:
                pdf_data = generate_pdf_report(result)
                st.download_button(
                    label="Download (.pdf)",
                    data=pdf_data,
                    file_name="stormwater_calculation.pdf",
                    mime="application/pdf"
                )

    # Footer
    st.markdown("---")
    st.markdown("""
    <div style="text-align: center; color: #7ec8e3; font-size: 1rem;">
        <p>⚠️ <strong>Disclaimer:</strong> This tool provides preliminary estimates only. 
        All calculations should be verified by a licensed professional engineer for final design.</p>
        <p>Optimized for Seattle/King County, WA. Rational Method limited to areas < 10 acres per local requirements.</p>
        <p style="margin-top: 15px;">
            <a href="https://www.seattle.gov/documents/Departments/SDCI/Codes/StormwaterCode/2021SWFullManualFinalClean.pdf" 
               target="_blank" style="color: #1c7585; font-size: 18px;">Seattle Stormwater Manual (2021)</a>
        </p>
        <p style="margin-top: 10px;">
            Stormwater Quick-Check v1.2 | Rational Method Calculator<br>
            Data: Seattle Stormwater Manual (2021), Table F.18 | King County SWDM (2021)
        </p>
<p style="margin-top: 6rem; color: rgb(28, 117, 133) !important;">
  Created by AlexEngineered. <br/>I'd love your feedback or suggestions.<br/>
  <a style="color: #7ec8e3;" href="https://docs.google.com/forms/d/e/1FAIpQLSdKmSf7U8lSopFQpIQgfGa3rfa6mwEBdpWWPuieIRk3vlGfrA/viewform" target="_blank">
    Send feedback via Google Forms
  </a>
</p>

</div>
    """, unsafe_allow_html=True)


if __name__ == "__main__":
    main()
