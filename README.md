# Stormwater Quick-Check Calculator

**Instant Runoff Estimator using the Rational Method (Q = CiA)**

A fast, free, web-based tool for civil engineers to calculate peak stormwater runoff for preliminary site assessments. Optimized for Seattle/King County, WA.

![Version](https://img.shields.io/badge/version-1.2-blue)
![Python](https://img.shields.io/badge/python-3.9+-green)
![License](https://img.shields.io/badge/license-MIT-lightgrey)

## Features

- **Address-based location input** — Geocoding via OpenStreetMap (no coordinates needed)
- **Embedded rainfall data** — Seattle Stormwater Manual (2021), Table F.18
- **Time of Concentration calculator** — FAA method with auto-suggested storm duration
- **Multiple surface types** — Composite site calculations with weighted C values
- **Instant results** — Rational Method calculation in seconds
- **PDF & text export** — Professional reports with citations for engineering documentation
- **Copy-ready reports** — Formatted output for engineering reports

## Quick Start

### Local Installation

```bash
# Clone or download the project
cd stormwater_quickcheck

# Install dependencies
pip install -r requirements.txt

# Run the app
streamlit run app.py
```

The app will open in your browser at `http://localhost:8501`

### Deploy to Streamlit Cloud (Free)

1. Push this folder to a GitHub repository
2. Go to [share.streamlit.io](https://share.streamlit.io)
3. Connect your GitHub repo
4. Deploy!

## How It Works

### The Rational Method

The calculator uses the Rational Method formula:

```
Q = C × i × A
```

Where:
- **Q** = Peak runoff rate (cubic feet per second, cfs)
- **C** = Runoff coefficient (dimensionless, 0-1)
- **i** = Rainfall intensity (inches per hour)
- **A** = Drainage area (acres)

### Time of Concentration (Tc)

The optional Tc calculator uses the FAA method, suitable for small urban drainage areas:

```
Tc = 1.8 × (1.1 - C) × L^0.5 / S^0.33
```

Per Seattle Stormwater Manual Appendix F: "The design storm duration shall equal the time of concentration."

### Data Sources

| Data | Source | Reference |
|------|--------|-----------|
| Rainfall Intensities | City of Seattle Stormwater Manual (July 2021) | Appendix F, Table F.18 |
| Runoff Coefficients | Seattle Stormwater Manual (2021) | Table F.19 |
| Runoff Coefficients | King County Surface Water Design Manual (2021, Amended 2024) | Section 3.2.1, Table 3.2.1.A |
| Geocoding | OpenStreetMap Nominatim | Current |

**Full Manual:** [Seattle Stormwater Manual (2021) PDF](https://www.seattle.gov/documents/Departments/SDCI/Codes/StormwaterCode/2021SWFullManualFinalClean.pdf)

### Runoff Coefficients

| Surface Type | C Value | Description |
|--------------|---------|-------------|
| Pavement and Roofs | 0.90 | Streets, parking lots, driveways, rooftops |
| Gravel Areas | 0.80 | Unpaved roads, gravel parking |
| Bare Soil | 0.60 | Exposed earth, construction sites |
| Lawns | 0.25 | Maintained grass areas |
| Landscaped Areas | 0.20 | Gardens, planted beds |
| Pasture | 0.20 | Pasture land, agricultural grass |
| Light Forest | 0.15 | Sparse tree cover, shrubs |
| Dense Forest | 0.10 | Undisturbed natural forest areas |
| Open Water | 1.00 | Ponds, lakes, wetlands |

## Limitations & Assumptions

### Method Limitations
- **Small watersheds only** — Rational Method valid for areas <10 acres per Seattle/King County requirements (extended limit: 50 acres with warnings)
- **Uniform rainfall assumed** — Does not account for spatial variation
- **Peak flow only** — Does not calculate runoff volume or hydrograph shape
- **Tc is preliminary** — FAA method provides estimate; verify with site-specific analysis

### Data Limitations
- **Seattle/King County optimized** — Rainfall data from Seattle Stormwater Manual Table F.18
- **Outside King County** — Tool displays warnings; verify rainfall data for your jurisdiction

### Regulatory Note
Both Seattle and King County require continuous simulation modeling (WWHM or MGSFlood) for most permit applications. This tool provides preliminary estimates for conveyance sizing only.

### Disclaimer

⚠️ **This tool is for preliminary planning purposes only.** All calculations should be independently verified by a licensed professional engineer for final design. Consult local jurisdiction requirements for applicable design standards.

## Project Structure

```
stormwater_app/
├── app.py              # Main Streamlit application
├── requirements.txt    # Python dependencies
└── README.md           # This file
```

## Dependencies

- streamlit >= 1.28.0
- requests >= 2.31.0
- reportlab >= 4.0.0


## Acknowledgments

- **City of Seattle** — Stormwater Manual and rainfall data
- **King County** — Surface Water Design Manual
- **OpenStreetMap** — Geocoding services

  ## License

Free to use and adapt for internal business, personal, or educational use.
Please don’t sell it or turn it into a paid product.

Licensed under Creative Commons Attribution–NonCommercial 4.0 (CC BY-NC 4.0)


## Acknowledgments

- **City of Seattle** — Stormwater Manual and rainfall data
- **King County** — Surface Water Design Manual
- **OpenStreetMap** — Geocoding services


## Feedback

Found a bug or have a suggestion? Send feedback to contact@alexengineered.com

---

*Built for civil engineers who value their time.*
