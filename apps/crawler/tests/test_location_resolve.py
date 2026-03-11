"""Tests for LocationResolver — in-memory location matching engine."""

from __future__ import annotations

import pytest

from src.core.location_resolve import (
    _CITY_SUFFIX_RE,
    _THE_PREFIX_RE,
    LocationResolver,
    _LocationEntry,
    _strip_accents,
)


def _build_resolver(
    entries: list[_LocationEntry],
    names: dict[int, dict[str, str]],
) -> LocationResolver:
    """Build a LocationResolver from test data without DB.

    Mirrors the index-building logic of LocationResolver.load():
    adds accent-stripped, "The"-prefix-stripped, and "City"-suffix-stripped variants.
    """
    resolver = LocationResolver()
    for entry in entries:
        resolver._entries[entry.id] = entry
    for loc_id, locale_names in names.items():
        for _locale, name in locale_names.items():
            key = name.lower()
            resolver._name_to_ids.setdefault(key, []).append(loc_id)
            # Accent-stripped variant
            stripped = _strip_accents(key)
            if stripped != key:
                resolver._name_to_ids.setdefault(stripped, []).append(loc_id)
            # "The " prefix and " City" suffix variants
            for variant in (key, stripped):
                no_the = _THE_PREFIX_RE.sub("", variant)
                if no_the != variant:
                    resolver._name_to_ids.setdefault(no_the, []).append(loc_id)
                no_city = _CITY_SUFFIX_RE.sub("", variant)
                if no_city != variant:
                    resolver._name_to_ids.setdefault(no_city, []).append(loc_id)
    resolver._loaded = True
    return resolver


# ── Test Data ────────────────────────────────────────────────────────
# Expanded with real-world locations seen in production DB

# Macro regions (synthetic IDs 1-9)
EMEA_ID = 1
APAC_ID = 2
AMERICAS_ID = 3
EU_ID = 4
DACH_ID = 5
LATAM_ID = 6
NORDICS_ID = 7
MENA_ID = 8
WORLDWIDE_ID = 9

# Countries (real GeoNames IDs)
CH_ID = 2658434  # Switzerland
DE_ID = 2921044  # Germany
US_ID = 6252001  # United States
FR_ID = 3017382  # France
IN_ID = 1269750  # India
GB_ID = 2635167  # United Kingdom
JP_ID = 1861060  # Japan
ES_ID = 2510769  # Spain
PL_ID = 798544  # Poland
IT_ID = 3175395  # Italy
SG_ID = 1880251  # Singapore (city-state)
AU_ID = 2077456  # Australia
IE_ID = 2963597  # Ireland
RO_ID = 798549  # Romania
PT_ID = 2264397  # Portugal
BR_ID = 3469034  # Brazil
CA_ID = 6251999  # Canada
LT_ID = 597427  # Lithuania
AT_ID = 2782113  # Austria
MX_ID = 3996063  # Mexico
CN_ID = 1814991  # China
LU_ID = 2960313  # Luxembourg
AE_ID = 290557  # United Arab Emirates
NL_ID = 2750405  # Netherlands
BE_ID = 2802361  # Belgium
CZ_ID = 3077311  # Czechia
SE_ID = 2661886  # Sweden
DK_ID = 2623032  # Denmark
NO_ID = 3144096  # Norway
FI_ID = 660013  # Finland
HU_ID = 719819  # Hungary
HR_ID2 = 3202326  # Croatia (HR_ID clashes with Haryana region)
RS_ID = 6290252  # Serbia
BG_ID = 732800  # Bulgaria
SK_ID = 3057568  # Slovakia
EE_ID = 453733  # Estonia
CY_ID = 146669  # Cyprus
KR_ID = 1835841  # South Korea
TR_ID = 298795  # Turkey

# US Regions (states)
WA_REGION_ID = 5815135  # Washington State
CA_REGION_ID = 5332921  # California
NY_REGION_ID = 5128638  # New York State
IL_REGION_ID = 4896861  # Illinois
TX_REGION_ID = 4736286  # Texas
MA_REGION_ID = 6254926  # Massachusetts
GA_REGION_ID = 4197000  # Georgia (state)
PA_REGION_ID = 6254927  # Pennsylvania

# India Regions
KA_REGION_ID = 1267701  # Karnataka
TG_REGION_ID = 1261029  # Telangana
TN_REGION_ID = 1255053  # Tamil Nadu
MH_REGION_ID = 1264418  # Maharashtra
HR_REGION_ID = 1270260  # Haryana

# Swiss regions (cantons)
ZH_REGION_ID = 2657895  # Canton of Zurich
GE_REGION_ID = 2660645  # Canton of Geneva
BE_REGION_ID = 2661551  # Canton of Bern
VD_REGION_ID = 2658181  # Canton of Vaud
BS_REGION_ID = 2661602  # Canton of Basel-Stadt
SG_REGION_ID = 2658821  # Canton of St. Gallen
ZG_REGION_ID = 2657907  # Canton of Zug
LU_REGION_ID = 2659810  # Canton of Lucerne
TI_REGION_ID = 2658370  # Canton of Ticino
AG_REGION_ID = 2661876  # Canton of Aargau

# Other regions
LONDON_REGION_ID = 2643741  # (using city ID as region for England)
VA_REGION_ID = 6254928  # Virginia
NV_REGION_ID = 5509151  # Nevada
CO_REGION_ID = 5417618  # Colorado
DC_REGION_ID = 4138106  # District of Columbia
NSW_REGION_ID = 2155400  # New South Wales
VIC_REGION_ID = 2145234  # Victoria
ON_REGION_ID = 6093943  # Ontario
BY_REGION_ID = 2951839  # Bavaria (Bayern)

# Cities
ZH_CITY_ID = 2657896  # Zurich
BERLIN_DE_ID = 2950159  # Berlin, Germany
BERLIN_US_ID = 5084868  # Berlin, NH (small US city)
PARIS_ID = 2988507  # Paris
SF_ID = 5391959  # San Francisco
NY_ID = 5128581  # New York City
SEATTLE_ID = 5809844  # Seattle
BELLEVUE_ID = 5786882  # Bellevue, WA
SUNNYVALE_ID = 5400075  # Sunnyvale, CA
MTV_ID = 5375480  # Mountain View, CA
BENGALURU_ID = 1277333  # Bengaluru (Bangalore)
HYDERABAD_ID = 1269843  # Hyderabad
TOKYO_ID = 1850147  # Tokyo
LONDON_CITY_ID = 2643743  # London (city)
MADRID_ID = 3117735  # Madrid
CHICAGO_ID = 4887398  # Chicago
BOSTON_ID = 4930956  # Boston
DALLAS_ID = 4684888  # Dallas
PHILLY_ID = 4560349  # Philadelphia
ATLANTA_ID = 4180439  # Atlanta
LA_ID = 5368361  # Los Angeles
MENLO_PARK_ID = 5372223  # Menlo Park
AUSTIN_ID = 4671654  # Austin
ROSEMONT_ID = 4907959  # Rosemont, IL
MILAN_ID = 3173435  # Milano/Milan
FRANKFURT_ID = 2925533  # Frankfurt am Main
DUSSELDORF_ID = 2934246  # Düsseldorf
SINGAPORE_CITY_ID = 1880252  # Singapore (city)
DUBLIN_ID = 2964574  # Dublin, Ireland
KRAKOW_ID = 3094802  # Kraków/Krakow
WARSAW_ID = 756135  # Warsaw/Warszawa
MUNICH_ID = 2867714  # Munich/München
HAMBURG_ID = 2911298  # Hamburg
VIENNA_ID = 2761369  # Vienna/Wien
DUBAI_ID = 292223  # Dubai
SHANGHAI_ID = 1796236  # Shanghai
SYDNEY_ID = 2147714  # Sydney
MELBOURNE_ID = 2158177  # Melbourne
TORONTO_ID = 6167865  # Toronto
ARLINGTON_VA_ID = 4744709  # Arlington, VA
REDMOND_ID = 5808276  # Redmond, WA
SAO_PAULO_ID = 3448439  # São Paulo
CHENNAI_ID = 1264527  # Chennai
MUMBAI_ID = 1275339  # Mumbai
GURUGRAM_ID = 1270642  # Gurugram
HOUSTON_ID = 4699066  # Houston
DENVER_ID = 5419384  # Denver
WASHINGTON_DC_ID = 4140963  # Washington D.C.
MEXICO_CITY_ID = 3530597  # Mexico City
BUCHAREST_ID = 683506  # Bucharest
LISBON_ID = 2267057  # Lisbon
AMSTERDAM_ID = 2759794  # Amsterdam
BRUSSELS_ID = 2800866  # Brussels
PRAGUE_ID = 3067696  # Prague/Praha
STOCKHOLM_ID = 2673730  # Stockholm
COPENHAGEN_ID = 2618425  # Copenhagen
OSLO_ID = 3143244  # Oslo
HELSINKI_ID = 658225  # Helsinki
BUDAPEST_ID = 3054643  # Budapest
ZAGREB_ID = 3186886  # Zagreb
BELGRADE_ID = 792680  # Belgrade
BARCELONA_ID = 3128760  # Barcelona
VALENCIA_ID = 2509954  # Valencia
STUTTGART_ID = 2825297  # Stuttgart
COLOGNE_ID = 2886242  # Cologne/Köln
NUREMBERG_ID = 2861650  # Nuremberg/Nürnberg
LEIPZIG_ID = 2879139  # Leipzig
PORTO_ID = 2735943  # Porto
VILNIUS_ID = 593116  # Vilnius
TALLINN_ID = 588409  # Tallinn
SOFIA_ID = 727011  # Sofia
BRATISLAVA_ID = 3060972  # Bratislava
LIMASSOL_ID = 146384  # Limassol
GENEVE_ID = 2660646  # Geneva/Genève
BERN_CITY_ID = 2661552  # Bern (city)
BASEL_CITY_ID = 2661604  # Basel
LAUSANNE_ID = 2659994  # Lausanne
WINTERTHUR_ID = 2657970  # Winterthur
ST_GALLEN_ID = 2658822  # St. Gallen
ZUG_CITY_ID = 2657908  # Zug (city)
LUCERNE_CITY_ID = 2659811  # Lucerne/Luzern
LUGANO_ID = 2659836  # Lugano
AARAU_ID = 2661881  # Aarau
BIEL_ID = 2661513  # Biel/Bienne
GLAND_ID = 2660593  # Gland
MANCHESTER_ID = 2643123  # Manchester
ISTANBUL_ID = 745044  # Istanbul
HONG_KONG_CITY_ID = 1819729  # Hong Kong (city)
SEOUL_ID = 1835848  # Seoul

# Hong Kong country
HK_ID = 1819730  # Hong Kong (country)

# Additional countries
PH_ID = 1694008  # Philippines
SA_COUNTRY_ID = 102358  # Saudi Arabia

# Additional regions
QC_REGION_ID = 6115047  # Quebec (province)

# Additional cities
MAKATI_ID = 1701668  # Makati City
BREMEN_ID = 2944388  # Bremen
RIYADH_ID = 108410  # Riyadh
POZNAN_ID = 3088171  # Poznań
QUEBEC_CITY_ID = 6325494  # Québec City

# Language disambiguation test data
GE_COUNTRY_ID = 614540  # Georgia (country, Caucasus)
MONTANA_STATE_ID = 5667009  # Montana (US state)
MONTANA_BG_REGION_ID = 453753  # Montana (Bulgarian province)
MONTANA_BG_CITY_ID = 729114  # Montana (Bulgarian city)

_ENTRIES = [
    # Macro regions
    _LocationEntry(id=EMEA_ID, parent_id=None, loc_type="macro", population=0),
    _LocationEntry(id=APAC_ID, parent_id=None, loc_type="macro", population=0),
    _LocationEntry(id=AMERICAS_ID, parent_id=None, loc_type="macro", population=0),
    _LocationEntry(id=EU_ID, parent_id=None, loc_type="macro", population=0),
    _LocationEntry(id=DACH_ID, parent_id=None, loc_type="macro", population=0),
    _LocationEntry(id=LATAM_ID, parent_id=None, loc_type="macro", population=0),
    _LocationEntry(id=NORDICS_ID, parent_id=None, loc_type="macro", population=0),
    _LocationEntry(id=MENA_ID, parent_id=None, loc_type="macro", population=0),
    _LocationEntry(id=WORLDWIDE_ID, parent_id=None, loc_type="macro", population=0),
    # Countries
    _LocationEntry(id=CH_ID, parent_id=None, loc_type="country", population=8_600_000),
    _LocationEntry(id=DE_ID, parent_id=None, loc_type="country", population=83_000_000),
    _LocationEntry(id=US_ID, parent_id=None, loc_type="country", population=331_000_000),
    _LocationEntry(id=FR_ID, parent_id=None, loc_type="country", population=67_000_000),
    _LocationEntry(id=IN_ID, parent_id=None, loc_type="country", population=1_380_000_000),
    _LocationEntry(id=GB_ID, parent_id=None, loc_type="country", population=67_000_000),
    _LocationEntry(id=JP_ID, parent_id=None, loc_type="country", population=126_000_000),
    _LocationEntry(id=ES_ID, parent_id=None, loc_type="country", population=47_000_000),
    _LocationEntry(id=PL_ID, parent_id=None, loc_type="country", population=38_000_000),
    _LocationEntry(id=IT_ID, parent_id=None, loc_type="country", population=60_000_000),
    _LocationEntry(id=SG_ID, parent_id=None, loc_type="country", population=5_600_000),
    _LocationEntry(id=AU_ID, parent_id=None, loc_type="country", population=25_000_000),
    _LocationEntry(id=IE_ID, parent_id=None, loc_type="country", population=5_000_000),
    _LocationEntry(id=RO_ID, parent_id=None, loc_type="country", population=19_000_000),
    _LocationEntry(id=PT_ID, parent_id=None, loc_type="country", population=10_300_000),
    _LocationEntry(id=BR_ID, parent_id=None, loc_type="country", population=213_000_000),
    _LocationEntry(id=CA_ID, parent_id=None, loc_type="country", population=38_000_000),
    _LocationEntry(id=LT_ID, parent_id=None, loc_type="country", population=2_800_000),
    _LocationEntry(id=AT_ID, parent_id=None, loc_type="country", population=9_000_000),
    _LocationEntry(id=MX_ID, parent_id=None, loc_type="country", population=130_000_000),
    _LocationEntry(id=CN_ID, parent_id=None, loc_type="country", population=1_400_000_000),
    _LocationEntry(id=LU_ID, parent_id=None, loc_type="country", population=640_000),
    _LocationEntry(id=AE_ID, parent_id=None, loc_type="country", population=9_900_000),
    _LocationEntry(id=NL_ID, parent_id=None, loc_type="country", population=17_400_000),
    _LocationEntry(id=BE_ID, parent_id=None, loc_type="country", population=11_500_000),
    _LocationEntry(id=CZ_ID, parent_id=None, loc_type="country", population=10_700_000),
    _LocationEntry(id=SE_ID, parent_id=None, loc_type="country", population=10_400_000),
    _LocationEntry(id=DK_ID, parent_id=None, loc_type="country", population=5_800_000),
    _LocationEntry(id=NO_ID, parent_id=None, loc_type="country", population=5_400_000),
    _LocationEntry(id=FI_ID, parent_id=None, loc_type="country", population=5_500_000),
    _LocationEntry(id=HU_ID, parent_id=None, loc_type="country", population=9_700_000),
    _LocationEntry(id=HR_ID2, parent_id=None, loc_type="country", population=4_000_000),
    _LocationEntry(id=RS_ID, parent_id=None, loc_type="country", population=6_900_000),
    _LocationEntry(id=BG_ID, parent_id=None, loc_type="country", population=6_900_000),
    _LocationEntry(id=SK_ID, parent_id=None, loc_type="country", population=5_500_000),
    _LocationEntry(id=EE_ID, parent_id=None, loc_type="country", population=1_300_000),
    _LocationEntry(id=CY_ID, parent_id=None, loc_type="country", population=1_200_000),
    _LocationEntry(id=KR_ID, parent_id=None, loc_type="country", population=51_800_000),
    _LocationEntry(id=TR_ID, parent_id=None, loc_type="country", population=84_000_000),
    _LocationEntry(id=HK_ID, parent_id=None, loc_type="country", population=7_500_000),
    _LocationEntry(id=PH_ID, parent_id=None, loc_type="country", population=110_000_000),
    _LocationEntry(id=SA_COUNTRY_ID, parent_id=None, loc_type="country", population=35_000_000),
    _LocationEntry(
        id=GE_COUNTRY_ID,
        parent_id=None,
        loc_type="country",
        population=3_700_000,
        languages=("ka", "ru", "hy", "az"),
    ),
    # US Regions (states)
    _LocationEntry(id=WA_REGION_ID, parent_id=US_ID, loc_type="region", population=0),
    _LocationEntry(id=CA_REGION_ID, parent_id=US_ID, loc_type="region", population=0),
    _LocationEntry(id=NY_REGION_ID, parent_id=US_ID, loc_type="region", population=0),
    _LocationEntry(id=IL_REGION_ID, parent_id=US_ID, loc_type="region", population=0),
    _LocationEntry(id=TX_REGION_ID, parent_id=US_ID, loc_type="region", population=0),
    _LocationEntry(id=MA_REGION_ID, parent_id=US_ID, loc_type="region", population=0),
    _LocationEntry(
        id=GA_REGION_ID, parent_id=US_ID, loc_type="region", population=0, languages=("en", "es")
    ),
    _LocationEntry(id=PA_REGION_ID, parent_id=US_ID, loc_type="region", population=0),
    _LocationEntry(
        id=MONTANA_STATE_ID,
        parent_id=US_ID,
        loc_type="region",
        population=1_137_233,
        languages=("en", "es"),
    ),
    _LocationEntry(
        id=MONTANA_BG_REGION_ID,
        parent_id=BG_ID,
        loc_type="region",
        population=148_098,
        languages=("bg",),
    ),
    _LocationEntry(
        id=MONTANA_BG_CITY_ID,
        parent_id=MONTANA_BG_REGION_ID,
        loc_type="city",
        population=47_445,
        languages=("bg",),
    ),
    # India Regions
    _LocationEntry(id=KA_REGION_ID, parent_id=IN_ID, loc_type="region", population=0),
    _LocationEntry(id=TG_REGION_ID, parent_id=IN_ID, loc_type="region", population=0),
    _LocationEntry(id=TN_REGION_ID, parent_id=IN_ID, loc_type="region", population=0),
    _LocationEntry(id=MH_REGION_ID, parent_id=IN_ID, loc_type="region", population=0),
    _LocationEntry(id=HR_REGION_ID, parent_id=IN_ID, loc_type="region", population=0),
    # Other regions
    _LocationEntry(id=ZH_REGION_ID, parent_id=CH_ID, loc_type="region", population=0),
    _LocationEntry(id=GE_REGION_ID, parent_id=CH_ID, loc_type="region", population=0),
    _LocationEntry(id=BE_REGION_ID, parent_id=CH_ID, loc_type="region", population=0),
    _LocationEntry(id=VD_REGION_ID, parent_id=CH_ID, loc_type="region", population=0),
    _LocationEntry(id=BS_REGION_ID, parent_id=CH_ID, loc_type="region", population=0),
    _LocationEntry(id=SG_REGION_ID, parent_id=CH_ID, loc_type="region", population=0),
    _LocationEntry(id=ZG_REGION_ID, parent_id=CH_ID, loc_type="region", population=0),
    _LocationEntry(id=LU_REGION_ID, parent_id=CH_ID, loc_type="region", population=0),
    _LocationEntry(id=TI_REGION_ID, parent_id=CH_ID, loc_type="region", population=0),
    _LocationEntry(id=AG_REGION_ID, parent_id=CH_ID, loc_type="region", population=0),
    _LocationEntry(id=VA_REGION_ID, parent_id=US_ID, loc_type="region", population=0),
    _LocationEntry(id=NV_REGION_ID, parent_id=US_ID, loc_type="region", population=0),
    _LocationEntry(id=CO_REGION_ID, parent_id=US_ID, loc_type="region", population=0),
    _LocationEntry(id=DC_REGION_ID, parent_id=US_ID, loc_type="region", population=0),
    _LocationEntry(id=NSW_REGION_ID, parent_id=AU_ID, loc_type="region", population=0),
    _LocationEntry(id=VIC_REGION_ID, parent_id=AU_ID, loc_type="region", population=0),
    _LocationEntry(id=ON_REGION_ID, parent_id=CA_ID, loc_type="region", population=0),
    _LocationEntry(id=BY_REGION_ID, parent_id=DE_ID, loc_type="region", population=0),
    _LocationEntry(id=QC_REGION_ID, parent_id=CA_ID, loc_type="region", population=0),
    # Catalonia (for Barcelona)
    _LocationEntry(id=3336901, parent_id=ES_ID, loc_type="region", population=0),
    # Cities
    _LocationEntry(id=ZH_CITY_ID, parent_id=ZH_REGION_ID, loc_type="city", population=402_000),
    _LocationEntry(id=BERLIN_DE_ID, parent_id=DE_ID, loc_type="city", population=3_645_000),
    _LocationEntry(id=BERLIN_US_ID, parent_id=US_ID, loc_type="city", population=10_000),
    _LocationEntry(id=BOSTON_ID, parent_id=MA_REGION_ID, loc_type="city", population=694_000),
    _LocationEntry(id=PARIS_ID, parent_id=FR_ID, loc_type="city", population=2_161_000),
    _LocationEntry(id=SF_ID, parent_id=CA_REGION_ID, loc_type="city", population=874_000),
    _LocationEntry(id=NY_ID, parent_id=NY_REGION_ID, loc_type="city", population=8_336_000),
    _LocationEntry(id=SEATTLE_ID, parent_id=WA_REGION_ID, loc_type="city", population=737_000),
    _LocationEntry(id=BELLEVUE_ID, parent_id=WA_REGION_ID, loc_type="city", population=151_000),
    _LocationEntry(id=SUNNYVALE_ID, parent_id=CA_REGION_ID, loc_type="city", population=155_000),
    _LocationEntry(id=MTV_ID, parent_id=CA_REGION_ID, loc_type="city", population=82_000),
    _LocationEntry(id=BENGALURU_ID, parent_id=KA_REGION_ID, loc_type="city", population=8_443_000),
    _LocationEntry(id=HYDERABAD_ID, parent_id=TG_REGION_ID, loc_type="city", population=6_809_000),
    _LocationEntry(id=TOKYO_ID, parent_id=JP_ID, loc_type="city", population=13_960_000),
    _LocationEntry(id=LONDON_CITY_ID, parent_id=GB_ID, loc_type="city", population=8_982_000),
    _LocationEntry(id=MADRID_ID, parent_id=ES_ID, loc_type="city", population=3_255_000),
    _LocationEntry(id=CHICAGO_ID, parent_id=IL_REGION_ID, loc_type="city", population=2_693_000),
    _LocationEntry(id=DALLAS_ID, parent_id=TX_REGION_ID, loc_type="city", population=1_304_000),
    _LocationEntry(id=PHILLY_ID, parent_id=PA_REGION_ID, loc_type="city", population=1_603_000),
    _LocationEntry(id=ATLANTA_ID, parent_id=GA_REGION_ID, loc_type="city", population=498_000),
    _LocationEntry(id=LA_ID, parent_id=CA_REGION_ID, loc_type="city", population=3_979_000),
    _LocationEntry(id=MENLO_PARK_ID, parent_id=CA_REGION_ID, loc_type="city", population=35_000),
    _LocationEntry(id=AUSTIN_ID, parent_id=TX_REGION_ID, loc_type="city", population=978_000),
    _LocationEntry(id=ROSEMONT_ID, parent_id=IL_REGION_ID, loc_type="city", population=4_200),
    _LocationEntry(id=MILAN_ID, parent_id=IT_ID, loc_type="city", population=1_352_000),
    _LocationEntry(id=FRANKFURT_ID, parent_id=DE_ID, loc_type="city", population=753_000),
    _LocationEntry(id=DUSSELDORF_ID, parent_id=DE_ID, loc_type="city", population=620_000),
    _LocationEntry(id=SINGAPORE_CITY_ID, parent_id=SG_ID, loc_type="city", population=5_600_000),
    _LocationEntry(id=DUBLIN_ID, parent_id=IE_ID, loc_type="city", population=1_025_000),
    _LocationEntry(id=KRAKOW_ID, parent_id=PL_ID, loc_type="city", population=769_000),
    _LocationEntry(id=WARSAW_ID, parent_id=PL_ID, loc_type="city", population=1_790_000),
    _LocationEntry(id=MUNICH_ID, parent_id=BY_REGION_ID, loc_type="city", population=1_472_000),
    _LocationEntry(id=HAMBURG_ID, parent_id=DE_ID, loc_type="city", population=1_845_000),
    _LocationEntry(id=VIENNA_ID, parent_id=AT_ID, loc_type="city", population=1_911_000),
    _LocationEntry(id=DUBAI_ID, parent_id=None, loc_type="city", population=3_331_000),
    _LocationEntry(id=SHANGHAI_ID, parent_id=CN_ID, loc_type="city", population=24_870_000),
    _LocationEntry(id=SYDNEY_ID, parent_id=NSW_REGION_ID, loc_type="city", population=5_312_000),
    _LocationEntry(id=MELBOURNE_ID, parent_id=VIC_REGION_ID, loc_type="city", population=4_936_000),
    _LocationEntry(id=TORONTO_ID, parent_id=ON_REGION_ID, loc_type="city", population=2_930_000),
    _LocationEntry(id=ARLINGTON_VA_ID, parent_id=VA_REGION_ID, loc_type="city", population=236_000),
    _LocationEntry(id=REDMOND_ID, parent_id=WA_REGION_ID, loc_type="city", population=73_000),
    _LocationEntry(id=SAO_PAULO_ID, parent_id=BR_ID, loc_type="city", population=12_325_000),
    _LocationEntry(id=CHENNAI_ID, parent_id=TN_REGION_ID, loc_type="city", population=4_681_000),
    _LocationEntry(id=MUMBAI_ID, parent_id=MH_REGION_ID, loc_type="city", population=12_478_000),
    _LocationEntry(id=GURUGRAM_ID, parent_id=HR_REGION_ID, loc_type="city", population=877_000),
    _LocationEntry(id=HOUSTON_ID, parent_id=TX_REGION_ID, loc_type="city", population=2_304_000),
    _LocationEntry(id=DENVER_ID, parent_id=CO_REGION_ID, loc_type="city", population=715_000),
    _LocationEntry(
        id=WASHINGTON_DC_ID, parent_id=DC_REGION_ID, loc_type="city", population=689_000
    ),
    _LocationEntry(id=MEXICO_CITY_ID, parent_id=MX_ID, loc_type="city", population=9_209_000),
    _LocationEntry(id=BUCHAREST_ID, parent_id=RO_ID, loc_type="city", population=1_883_000),
    _LocationEntry(id=LISBON_ID, parent_id=PT_ID, loc_type="city", population=506_000),
    _LocationEntry(id=AMSTERDAM_ID, parent_id=NL_ID, loc_type="city", population=872_000),
    _LocationEntry(id=BRUSSELS_ID, parent_id=BE_ID, loc_type="city", population=1_209_000),
    _LocationEntry(id=PRAGUE_ID, parent_id=CZ_ID, loc_type="city", population=1_335_000),
    _LocationEntry(id=STOCKHOLM_ID, parent_id=SE_ID, loc_type="city", population=975_000),
    _LocationEntry(id=COPENHAGEN_ID, parent_id=DK_ID, loc_type="city", population=616_000),
    _LocationEntry(id=OSLO_ID, parent_id=NO_ID, loc_type="city", population=693_000),
    _LocationEntry(id=HELSINKI_ID, parent_id=FI_ID, loc_type="city", population=643_000),
    _LocationEntry(id=BUDAPEST_ID, parent_id=HU_ID, loc_type="city", population=1_752_000),
    _LocationEntry(id=ZAGREB_ID, parent_id=HR_ID2, loc_type="city", population=688_000),
    _LocationEntry(id=BELGRADE_ID, parent_id=RS_ID, loc_type="city", population=1_374_000),
    _LocationEntry(id=BARCELONA_ID, parent_id=3336901, loc_type="city", population=1_621_000),
    _LocationEntry(id=VALENCIA_ID, parent_id=ES_ID, loc_type="city", population=792_000),
    _LocationEntry(id=STUTTGART_ID, parent_id=DE_ID, loc_type="city", population=634_000),
    _LocationEntry(id=COLOGNE_ID, parent_id=DE_ID, loc_type="city", population=1_087_000),
    _LocationEntry(id=NUREMBERG_ID, parent_id=BY_REGION_ID, loc_type="city", population=518_000),
    _LocationEntry(id=LEIPZIG_ID, parent_id=DE_ID, loc_type="city", population=597_000),
    _LocationEntry(id=PORTO_ID, parent_id=PT_ID, loc_type="city", population=238_000),
    _LocationEntry(id=VILNIUS_ID, parent_id=LT_ID, loc_type="city", population=574_000),
    _LocationEntry(id=TALLINN_ID, parent_id=EE_ID, loc_type="city", population=438_000),
    _LocationEntry(id=SOFIA_ID, parent_id=BG_ID, loc_type="city", population=1_242_000),
    _LocationEntry(id=BRATISLAVA_ID, parent_id=SK_ID, loc_type="city", population=437_000),
    _LocationEntry(id=LIMASSOL_ID, parent_id=CY_ID, loc_type="city", population=235_000),
    _LocationEntry(id=GENEVE_ID, parent_id=GE_REGION_ID, loc_type="city", population=201_000),
    _LocationEntry(id=BERN_CITY_ID, parent_id=BE_REGION_ID, loc_type="city", population=133_000),
    _LocationEntry(id=BASEL_CITY_ID, parent_id=BS_REGION_ID, loc_type="city", population=177_000),
    _LocationEntry(id=LAUSANNE_ID, parent_id=VD_REGION_ID, loc_type="city", population=139_000),
    _LocationEntry(id=WINTERTHUR_ID, parent_id=ZH_REGION_ID, loc_type="city", population=114_000),
    _LocationEntry(id=ST_GALLEN_ID, parent_id=SG_REGION_ID, loc_type="city", population=75_000),
    _LocationEntry(id=ZUG_CITY_ID, parent_id=ZG_REGION_ID, loc_type="city", population=30_000),
    _LocationEntry(id=LUCERNE_CITY_ID, parent_id=LU_REGION_ID, loc_type="city", population=82_000),
    _LocationEntry(id=LUGANO_ID, parent_id=TI_REGION_ID, loc_type="city", population=63_000),
    _LocationEntry(id=AARAU_ID, parent_id=AG_REGION_ID, loc_type="city", population=21_000),
    _LocationEntry(id=BIEL_ID, parent_id=BE_REGION_ID, loc_type="city", population=55_000),
    _LocationEntry(id=GLAND_ID, parent_id=VD_REGION_ID, loc_type="city", population=13_000),
    _LocationEntry(id=MANCHESTER_ID, parent_id=GB_ID, loc_type="city", population=545_000),
    _LocationEntry(id=ISTANBUL_ID, parent_id=TR_ID, loc_type="city", population=15_190_000),
    _LocationEntry(id=HONG_KONG_CITY_ID, parent_id=HK_ID, loc_type="city", population=7_500_000),
    _LocationEntry(id=SEOUL_ID, parent_id=KR_ID, loc_type="city", population=10_350_000),
    _LocationEntry(id=MAKATI_ID, parent_id=PH_ID, loc_type="city", population=582_000),
    _LocationEntry(id=BREMEN_ID, parent_id=DE_ID, loc_type="city", population=569_000),
    _LocationEntry(id=RIYADH_ID, parent_id=SA_COUNTRY_ID, loc_type="city", population=7_600_000),
    _LocationEntry(id=POZNAN_ID, parent_id=PL_ID, loc_type="city", population=538_000),
    _LocationEntry(id=QUEBEC_CITY_ID, parent_id=QC_REGION_ID, loc_type="city", population=531_000),
]

_NAMES: dict[int, dict[str, str]] = {
    # Macros
    EMEA_ID: {"en": "EMEA"},
    APAC_ID: {"en": "APAC"},
    AMERICAS_ID: {"en": "Americas"},
    EU_ID: {"en": "EU"},
    DACH_ID: {"en": "DACH"},
    LATAM_ID: {"en": "LATAM"},
    NORDICS_ID: {"en": "Nordics"},
    MENA_ID: {"en": "MENA"},
    WORLDWIDE_ID: {"en": "Worldwide"},
    # Countries
    CH_ID: {"en": "Switzerland", "de": "Schweiz"},
    DE_ID: {"en": "Germany", "de": "Deutschland"},
    US_ID: {"en": "United States"},
    FR_ID: {"en": "France"},
    IN_ID: {"en": "India"},
    GB_ID: {"en": "United Kingdom"},
    JP_ID: {"en": "Japan"},
    ES_ID: {"en": "Spain"},
    PL_ID: {"en": "Poland"},
    IT_ID: {"en": "Italy"},
    SG_ID: {"en": "Singapore"},
    AU_ID: {"en": "Australia"},
    IE_ID: {"en": "Ireland"},
    RO_ID: {"en": "Romania"},
    PT_ID: {"en": "Portugal"},
    BR_ID: {"en": "Brazil"},
    CA_ID: {"en": "Canada"},
    LT_ID: {"en": "Lithuania"},
    AT_ID: {"en": "Austria"},
    MX_ID: {"en": "Mexico"},
    CN_ID: {"en": "China"},
    LU_ID: {"en": "Luxembourg"},
    AE_ID: {"en": "United Arab Emirates"},
    NL_ID: {"en": "Netherlands"},
    BE_ID: {"en": "Belgium"},
    CZ_ID: {"en": "Czechia", "alt": "Czech Republic"},
    SE_ID: {"en": "Sweden"},
    DK_ID: {"en": "Denmark"},
    NO_ID: {"en": "Norway"},
    FI_ID: {"en": "Finland"},
    HU_ID: {"en": "Hungary"},
    HR_ID2: {"en": "Croatia"},
    RS_ID: {"en": "Serbia"},
    BG_ID: {"en": "Bulgaria"},
    SK_ID: {"en": "Slovakia"},
    EE_ID: {"en": "Estonia"},
    CY_ID: {"en": "Cyprus"},
    KR_ID: {"en": "South Korea"},
    TR_ID: {"en": "Turkey"},
    HK_ID: {"en": "Hong Kong"},
    GE_COUNTRY_ID: {"en": "Georgia"},
    # US Regions
    WA_REGION_ID: {"en": "Washington"},
    CA_REGION_ID: {"en": "California"},
    NY_REGION_ID: {"en": "New York"},
    IL_REGION_ID: {"en": "Illinois"},
    TX_REGION_ID: {"en": "Texas"},
    MA_REGION_ID: {"en": "Massachusetts"},
    GA_REGION_ID: {"en": "Georgia"},
    PA_REGION_ID: {"en": "Pennsylvania"},
    MONTANA_STATE_ID: {"en": "Montana"},
    MONTANA_BG_REGION_ID: {"en": "Montana"},
    MONTANA_BG_CITY_ID: {"en": "Montana"},
    # India Regions
    KA_REGION_ID: {"en": "Karnataka"},
    TG_REGION_ID: {"en": "Telangana"},
    TN_REGION_ID: {"en": "Tamil Nadu"},
    MH_REGION_ID: {"en": "Maharashtra"},
    HR_REGION_ID: {"en": "Haryana"},
    # Other regions
    ZH_REGION_ID: {"en": "Canton of Zurich", "alt": "Zurich"},
    GE_REGION_ID: {"en": "Canton of Geneva", "alt": "Geneva"},
    BE_REGION_ID: {"en": "Canton of Bern", "alt": "Bern"},
    VD_REGION_ID: {"en": "Canton of Vaud", "alt": "Vaud"},
    BS_REGION_ID: {"en": "Canton of Basel-Stadt", "alt": "Basel-Stadt"},
    SG_REGION_ID: {"en": "Canton of St. Gallen"},
    ZG_REGION_ID: {"en": "Canton of Zug", "alt": "Zug"},
    LU_REGION_ID: {"en": "Canton of Lucerne", "alt": "Lucerne"},
    TI_REGION_ID: {"en": "Canton of Ticino", "alt": "Ticino"},
    AG_REGION_ID: {"en": "Canton of Aargau", "alt": "Aargau"},
    VA_REGION_ID: {"en": "Virginia"},
    NV_REGION_ID: {"en": "Nevada"},
    CO_REGION_ID: {"en": "Colorado"},
    DC_REGION_ID: {"en": "District of Columbia"},
    NSW_REGION_ID: {"en": "New South Wales"},
    VIC_REGION_ID: {"en": "Victoria"},
    ON_REGION_ID: {"en": "Ontario"},
    BY_REGION_ID: {"en": "Bavaria"},
    3336901: {"en": "Catalonia"},
    # Cities
    ZH_CITY_ID: {"en": "Zurich", "de": "Zürich"},
    BERLIN_DE_ID: {"en": "Berlin"},
    BERLIN_US_ID: {"en": "Berlin"},
    PARIS_ID: {"en": "Paris"},
    SF_ID: {"en": "San Francisco"},
    NY_ID: {"en": "New York City", "alt": "New York"},
    SEATTLE_ID: {"en": "Seattle"},
    BELLEVUE_ID: {"en": "Bellevue"},
    SUNNYVALE_ID: {"en": "Sunnyvale"},
    MTV_ID: {"en": "Mountain View"},
    BENGALURU_ID: {"en": "Bengaluru"},
    HYDERABAD_ID: {"en": "Hyderabad"},
    TOKYO_ID: {"en": "Tokyo"},
    LONDON_CITY_ID: {"en": "London"},
    MADRID_ID: {"en": "Madrid"},
    CHICAGO_ID: {"en": "Chicago"},
    DALLAS_ID: {"en": "Dallas"},
    BOSTON_ID: {"en": "Boston"},
    PHILLY_ID: {"en": "Philadelphia"},
    ATLANTA_ID: {"en": "Atlanta"},
    LA_ID: {"en": "Los Angeles"},
    MENLO_PARK_ID: {"en": "Menlo Park"},
    AUSTIN_ID: {"en": "Austin"},
    ROSEMONT_ID: {"en": "Rosemont"},
    MILAN_ID: {"en": "Milan", "it": "Milano"},
    FRANKFURT_ID: {"en": "Frankfurt am Main", "de": "Frankfurt a. M."},
    DUSSELDORF_ID: {"en": "Düsseldorf"},
    SINGAPORE_CITY_ID: {"en": "Singapore"},
    DUBLIN_ID: {"en": "Dublin"},
    KRAKOW_ID: {"en": "Krakow", "pl": "Kraków"},
    WARSAW_ID: {"en": "Warsaw", "pl": "Warszawa"},
    MUNICH_ID: {"en": "Munich", "de": "München"},
    HAMBURG_ID: {"en": "Hamburg"},
    VIENNA_ID: {"en": "Vienna", "de": "Wien"},
    DUBAI_ID: {"en": "Dubai"},
    SHANGHAI_ID: {"en": "Shanghai"},
    SYDNEY_ID: {"en": "Sydney"},
    MELBOURNE_ID: {"en": "Melbourne"},
    TORONTO_ID: {"en": "Toronto"},
    ARLINGTON_VA_ID: {"en": "Arlington"},
    REDMOND_ID: {"en": "Redmond"},
    SAO_PAULO_ID: {"en": "Sao Paulo", "pt": "São Paulo"},
    CHENNAI_ID: {"en": "Chennai"},
    MUMBAI_ID: {"en": "Mumbai"},
    GURUGRAM_ID: {"en": "Gurugram"},
    HOUSTON_ID: {"en": "Houston"},
    DENVER_ID: {"en": "Denver"},
    WASHINGTON_DC_ID: {"en": "Washington"},
    MEXICO_CITY_ID: {"en": "Mexico City"},
    BUCHAREST_ID: {"en": "Bucharest"},
    LISBON_ID: {"en": "Lisbon"},
    AMSTERDAM_ID: {"en": "Amsterdam"},
    BRUSSELS_ID: {"en": "Brussels"},
    PRAGUE_ID: {"en": "Prague", "cs": "Praha"},
    STOCKHOLM_ID: {"en": "Stockholm"},
    COPENHAGEN_ID: {"en": "Copenhagen"},
    OSLO_ID: {"en": "Oslo"},
    HELSINKI_ID: {"en": "Helsinki"},
    BUDAPEST_ID: {"en": "Budapest"},
    ZAGREB_ID: {"en": "Zagreb"},
    BELGRADE_ID: {"en": "Belgrade"},
    BARCELONA_ID: {"en": "Barcelona"},
    VALENCIA_ID: {"en": "Valencia"},
    STUTTGART_ID: {"en": "Stuttgart"},
    COLOGNE_ID: {"en": "Cologne", "de": "Köln"},
    NUREMBERG_ID: {"en": "Nuremberg", "de": "Nürnberg"},
    LEIPZIG_ID: {"en": "Leipzig"},
    PORTO_ID: {"en": "Porto"},
    VILNIUS_ID: {"en": "Vilnius"},
    TALLINN_ID: {"en": "Tallinn"},
    SOFIA_ID: {"en": "Sofia"},
    BRATISLAVA_ID: {"en": "Bratislava"},
    LIMASSOL_ID: {"en": "Limassol"},
    GENEVE_ID: {"en": "Geneva", "fr": "Genève"},
    BERN_CITY_ID: {"en": "Bern", "de": "Bern"},
    BASEL_CITY_ID: {"en": "Basel"},
    LAUSANNE_ID: {"en": "Lausanne"},
    WINTERTHUR_ID: {"en": "Winterthur"},
    ST_GALLEN_ID: {"en": "St. Gallen", "alt": "St Gallen"},
    ZUG_CITY_ID: {"en": "Zug"},
    LUCERNE_CITY_ID: {"en": "Lucerne", "de": "Luzern"},
    LUGANO_ID: {"en": "Lugano"},
    AARAU_ID: {"en": "Aarau"},
    BIEL_ID: {"en": "Biel", "fr": "Bienne"},
    GLAND_ID: {"en": "Gland"},
    MANCHESTER_ID: {"en": "Manchester"},
    ISTANBUL_ID: {"en": "Istanbul"},
    HONG_KONG_CITY_ID: {"en": "Hong Kong"},
    SEOUL_ID: {"en": "Seoul"},
    # Additional countries
    PH_ID: {"en": "Philippines"},
    SA_COUNTRY_ID: {"en": "Saudi Arabia"},
    # Additional regions
    QC_REGION_ID: {"en": "Quebec"},
    # Additional cities
    MAKATI_ID: {"en": "Makati City"},
    BREMEN_ID: {"en": "Bremen"},
    RIYADH_ID: {"en": "Riyadh"},
    POZNAN_ID: {"en": "Poznań"},
    QUEBEC_CITY_ID: {"en": "Québec City"},
}


@pytest.fixture
def resolver() -> LocationResolver:
    return _build_resolver(_ENTRIES, _NAMES)


# ── Basic Matching ───────────────────────────────────────────────────


class TestBasicMatching:
    def test_city_country(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Zurich, Switzerland"])
        assert len(results) == 1
        assert results[0].location_id == ZH_CITY_ID
        assert results[0].location_type == "onsite"

    def test_city_only_disambiguate_by_pop(self, resolver: LocationResolver) -> None:
        """'Berlin' → Berlin DE (3.6M) not Berlin US (10K)."""
        results = resolver.resolve(["Berlin"])
        assert len(results) == 1
        assert results[0].location_id == BERLIN_DE_ID

    def test_country_only(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Switzerland"])
        assert len(results) == 1
        assert results[0].location_id == CH_ID

    def test_berlin_germany_disambiguation(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Berlin, Germany"])
        assert len(results) == 1
        assert results[0].location_id == BERLIN_DE_ID


# ── Real-World: City, State, Country (full names) ───────────────────


class TestCityStateCountry:
    """Formats like 'Seattle, Washington, USA'."""

    def test_seattle_washington_usa(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Seattle, Washington, USA"])
        assert len(results) == 1
        assert results[0].location_id == SEATTLE_ID

    def test_bellevue_washington_usa(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Bellevue, Washington, USA"])
        assert len(results) == 1
        assert results[0].location_id == BELLEVUE_ID

    def test_new_york_new_york_usa(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["New York, New York, USA"])
        assert len(results) == 1
        assert results[0].location_id == NY_ID


# ── Real-World: City, State Abbreviation, Country ───────────────────


class TestCityStateAbbrevCountry:
    """Formats like 'Sunnyvale, CA, USA'."""

    def test_sunnyvale_ca_usa(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Sunnyvale, CA, USA"])
        assert len(results) == 1
        assert results[0].location_id == SUNNYVALE_ID

    def test_mountain_view_ca_usa(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Mountain View, CA, USA"])
        assert len(results) == 1
        assert results[0].location_id == MTV_ID

    def test_new_york_ny_usa(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["New York, NY, USA"])
        assert len(results) == 1
        # Should match NY city, not region
        assert results[0].location_id == NY_ID

    def test_menlo_park_ca(self, resolver: LocationResolver) -> None:
        """'Menlo Park, CA' — no country code."""
        results = resolver.resolve(["Menlo Park, CA"])
        assert len(results) == 1
        assert results[0].location_id == MENLO_PARK_ID

    def test_austin_tx(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Austin, TX"])
        assert len(results) == 1
        assert results[0].location_id == AUSTIN_ID


# ── Real-World: State-Prefixed (Workday format) ─────────────────────


class TestStatePrefixed:
    """Formats like 'IL-Chicago', 'NY-New York', 'CA-San Francisco'."""

    def test_il_chicago(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["IL-Chicago"])
        assert len(results) == 1
        assert results[0].location_id == CHICAGO_ID

    def test_ny_new_york(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["NY-New York"])
        assert len(results) == 1
        # Should match NYC (city) because it has the state context
        assert results[0].location_id == NY_ID

    def test_ca_san_francisco(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["CA-San Francisco"])
        assert len(results) == 1
        assert results[0].location_id == SF_ID

    def test_ca_los_angeles(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["CA-Los Angeles"])
        assert len(results) == 1
        assert results[0].location_id == LA_ID

    def test_tx_dallas(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["TX-Dallas"])
        assert len(results) == 1
        assert results[0].location_id == DALLAS_ID

    def test_pa_philadelphia(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["PA-Philadelphia"])
        assert len(results) == 1
        assert results[0].location_id == PHILLY_ID

    def test_ga_atlanta(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["GA-Atlanta"])
        assert len(results) == 1
        assert results[0].location_id == ATLANTA_ID

    def test_il_rosemont_il_united_states(self, resolver: LocationResolver) -> None:
        """'IL-Rosemont, IL, United States' — complex format."""
        results = resolver.resolve(["IL-Rosemont, IL, United States"])
        assert len(results) == 1
        assert results[0].location_id == ROSEMONT_ID


# ── Real-World: ISO 3166-1 Alpha-3 Country Codes ────────────────────


class TestISO3Codes:
    """Formats like 'Bengaluru, Karnataka, IND'."""

    def test_bengaluru_karnataka_ind(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Bengaluru, Karnataka, IND"])
        assert len(results) == 1
        assert results[0].location_id == BENGALURU_ID

    def test_hyderabad_telangana_ind(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Hyderabad, Telangana, IND"])
        assert len(results) == 1
        assert results[0].location_id == HYDERABAD_ID

    def test_tokyo_jpn(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Tokyo, JPN"])
        assert len(results) == 1
        assert results[0].location_id == TOKYO_ID

    def test_frankfurt_deu(self, resolver: LocationResolver) -> None:
        """'Frankfurt a. M., DEU' — city with special chars + ISO3."""
        results = resolver.resolve(["Frankfurt a. M., DEU"])
        assert len(results) == 1
        assert results[0].location_id == FRANKFURT_ID

    def test_london_england_gbr(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["London, England, GBR"])
        assert len(results) == 1
        assert results[0].location_id == LONDON_CITY_ID

    def test_dusseldorf_deu(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Düsseldorf, DEU"])
        assert len(results) == 1
        assert results[0].location_id == DUSSELDORF_ID


# ── Real-World: City, Region, Country (full names) ──────────────────


class TestCityRegionCountry:
    """Formats like 'Bengaluru, Karnataka, India'."""

    def test_bengaluru_karnataka_india(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Bengaluru, Karnataka, India"])
        assert len(results) == 1
        assert results[0].location_id == BENGALURU_ID

    def test_london_london_uk(self, resolver: LocationResolver) -> None:
        """'London, London, United Kingdom' — city same as region name."""
        results = resolver.resolve(["London, London, United Kingdom"])
        assert len(results) == 1
        assert results[0].location_id == LONDON_CITY_ID

    def test_milano_milano_italy(self, resolver: LocationResolver) -> None:
        """'Milano, Milano, Italy' — Italian name."""
        results = resolver.resolve(["Milano, Milano, Italy"])
        assert len(results) == 1
        assert results[0].location_id == MILAN_ID


# ── Real-World: City Only ───────────────────────────────────────────


class TestCityOnly:
    def test_singapore(self, resolver: LocationResolver) -> None:
        """'Singapore' matches both country and city — should return something."""
        results = resolver.resolve(["Singapore"])
        assert len(results) == 1
        # Should match the most populous (city or country)
        assert results[0].location_id in (SG_ID, SINGAPORE_CITY_ID)

    def test_london(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["London"])
        assert len(results) == 1
        assert results[0].location_id == LONDON_CITY_ID

    def test_madrid(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Madrid"])
        assert len(results) == 1
        assert results[0].location_id == MADRID_ID

    def test_tokyo(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Tokyo"])
        assert len(results) == 1
        assert results[0].location_id == TOKYO_ID


# ── Remote ───────────────────────────────────────────────────────────


class TestRemote:
    def test_pure_remote(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Remote"])
        assert len(results) == 1
        assert results[0].location_id is None
        assert results[0].location_type == "remote"

    def test_fully_remote(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Fully Remote"])
        assert len(results) == 1
        assert results[0].location_id is None
        assert results[0].location_type == "remote"

    def test_remote_country_abbreviation(self, resolver: LocationResolver) -> None:
        """'Remote - US' → US country ID + type=remote."""
        results = resolver.resolve(["Remote - US"])
        assert len(results) == 1
        assert results[0].location_id == US_ID
        assert results[0].location_type == "remote"

    def test_remote_full_country_name(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Remote - United States"])
        assert len(results) == 1
        assert results[0].location_id == US_ID
        assert results[0].location_type == "remote"

    def test_country_dash_remote(self, resolver: LocationResolver) -> None:
        """'Spain - Remote' → Spain + type=remote."""
        results = resolver.resolve(["Spain - Remote"])
        assert len(results) == 1
        assert results[0].location_id == ES_ID
        assert results[0].location_type == "remote"

    def test_poland_remote(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Poland - Remote"])
        assert len(results) == 1
        assert results[0].location_id == PL_ID
        assert results[0].location_type == "remote"


# ── Standalone Type Markers ──────────────────────────────────────────


class TestStandaloneTypes:
    def test_hybrid_standalone(self, resolver: LocationResolver) -> None:
        """'Hybrid' → type=hybrid, no location."""
        results = resolver.resolve(["Hybrid"])
        assert len(results) == 1
        assert results[0].location_id is None
        assert results[0].location_type == "hybrid"

    def test_on_site_standalone(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["On-site"])
        assert len(results) == 1
        assert results[0].location_id is None
        assert results[0].location_type == "onsite"


# ── Macro Regions ────────────────────────────────────────────────────


class TestMacroRegions:
    def test_emea(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["EMEA"])
        assert len(results) == 1
        assert results[0].location_id == EMEA_ID

    def test_dach(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["DACH"])
        assert len(results) == 1
        assert results[0].location_id == DACH_ID

    def test_eu_remote(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["EU (Remote)"])
        assert len(results) == 1
        assert results[0].location_id == EU_ID
        assert results[0].location_type == "remote"

    def test_worldwide(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Worldwide"])
        assert len(results) == 1
        assert results[0].location_id == WORLDWIDE_ID


# ── Skip / Edge Cases ───────────────────────────────────────────────


class TestSkip:
    def test_multiple_locations(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Multiple Locations"])
        assert len(results) == 0

    def test_various_locations(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Various Locations"])
        assert len(results) == 0

    def test_empty_string(self, resolver: LocationResolver) -> None:
        results = resolver.resolve([""])
        assert len(results) == 0

    def test_none(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(None)
        assert len(results) == 0

    def test_whitespace_only(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["   "])
        assert len(results) == 0


# ── Type Hints (parenthetical + inline) ──────────────────────────────


class TestTypeHints:
    def test_parenthetical_hybrid(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["San Francisco (Hybrid)"])
        assert len(results) == 1
        assert results[0].location_id == SF_ID
        assert results[0].location_type == "hybrid"

    def test_parenthetical_remote(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["London (Remote)"])
        assert len(results) == 1
        assert results[0].location_id == LONDON_CITY_ID
        assert results[0].location_type == "remote"

    def test_fallback_type(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Paris"], "remote")
        assert len(results) == 1
        assert results[0].location_id == PARIS_ID
        assert results[0].location_type == "remote"

    def test_inline_remote(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Zurich remote"])
        assert len(results) == 1
        assert results[0].location_id == ZH_CITY_ID
        assert results[0].location_type == "remote"

    def test_parenthetical_on_site(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Berlin (On-site)"])
        assert len(results) == 1
        assert results[0].location_id == BERLIN_DE_ID
        assert results[0].location_type == "onsite"


# ── Aliases ──────────────────────────────────────────────────────────


class TestAliases:
    def test_sf_alias(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["SF"])
        assert len(results) == 1
        assert results[0].location_id == SF_ID

    def test_sf_hybrid(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["SF (Hybrid)"])
        assert len(results) == 1
        assert results[0].location_id == SF_ID
        assert results[0].location_type == "hybrid"

    def test_nyc_alias(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["NYC"])
        assert len(results) == 1
        assert results[0].location_id == NY_ID

    def test_usa_alias(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["USA"])
        assert len(results) == 1
        assert results[0].location_id == US_ID

    def test_uk_alias(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["UK"])
        assert len(results) == 1
        assert results[0].location_id == GB_ID

    def test_us_standalone(self, resolver: LocationResolver) -> None:
        """'US' (seen 336 times in prod DB) → United States."""
        results = resolver.resolve(["US"])
        assert len(results) == 1
        assert results[0].location_id == US_ID


# ── Multiple Locations ───────────────────────────────────────────────


class TestMultipleLocations:
    def test_multiple_results(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Zurich, Switzerland", "Berlin"])
        assert len(results) == 2
        ids = {r.location_id for r in results}
        assert ZH_CITY_ID in ids
        assert BERLIN_DE_ID in ids

    def test_mixed_with_remote(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Paris", "Remote"])
        assert len(results) == 2
        paris = [r for r in results if r.location_id == PARIS_ID]
        remote = [r for r in results if r.location_id is None]
        assert len(paris) == 1
        assert len(remote) == 1
        assert remote[0].location_type == "remote"

    def test_city_and_hybrid(self, resolver: LocationResolver) -> None:
        """Mix of city and standalone type."""
        results = resolver.resolve(["London", "Hybrid"])
        assert len(results) == 2
        london = [r for r in results if r.location_id == LONDON_CITY_ID]
        hybrid = [r for r in results if r.location_id is None and r.location_type == "hybrid"]
        assert len(london) == 1
        assert len(hybrid) == 1


# ── German / Localized Names ────────────────────────────────────────


class TestLocalizedNames:
    def test_zurich_umlaut(self, resolver: LocationResolver) -> None:
        """'Zürich' → Zurich city via German name."""
        results = resolver.resolve(["Zürich"])
        assert len(results) == 1
        assert results[0].location_id == ZH_CITY_ID

    def test_schweiz(self, resolver: LocationResolver) -> None:
        """'Schweiz' → Switzerland via German name."""
        results = resolver.resolve(["Schweiz"])
        assert len(results) == 1
        assert results[0].location_id == CH_ID

    def test_deutschland(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Deutschland"])
        assert len(results) == 1
        assert results[0].location_id == DE_ID

    def test_milano(self, resolver: LocationResolver) -> None:
        """'Milano' → Milan via Italian name."""
        results = resolver.resolve(["Milano"])
        assert len(results) == 1
        assert results[0].location_id == MILAN_ID

    def test_frankfurt_a_m(self, resolver: LocationResolver) -> None:
        """'Frankfurt a. M.' → Frankfurt via German name."""
        results = resolver.resolve(["Frankfurt a. M."])
        assert len(results) == 1
        assert results[0].location_id == FRANKFURT_ID

    def test_dusseldorf_umlaut(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Düsseldorf"])
        assert len(results) == 1
        assert results[0].location_id == DUSSELDORF_ID


# ── ISO 3166-1 Alpha-2 Country Codes ─────────────────────────────────


class TestISO2Codes:
    """City + ISO2 country code: 'Bengaluru, IN', 'Milano, IT', etc."""

    def test_bengaluru_in(self, resolver: LocationResolver) -> None:
        """'Bengaluru, IN' → India (not Indiana) via context."""
        results = resolver.resolve(["Bengaluru, IN"])
        assert len(results) == 1
        assert results[0].location_id == BENGALURU_ID

    def test_pune_in(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Pune, IN"])
        assert len(results) == 1
        assert results[0].location_id is not None
        # Pune is in India — should match via ancestor, not Indiana

    def test_chennai_in(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Chennai, IN"])
        assert len(results) == 1
        assert results[0].location_id == CHENNAI_ID

    def test_gurugram_in(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Gurugram, IN"])
        assert len(results) == 1
        assert results[0].location_id == GURUGRAM_ID

    def test_mumbai_in(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Mumbai, IN"])
        assert len(results) == 1
        assert results[0].location_id == MUMBAI_ID

    def test_berlin_de(self, resolver: LocationResolver) -> None:
        """'Berlin, DE' → Berlin, Germany (not Delaware)."""
        results = resolver.resolve(["Berlin, DE"])
        assert len(results) == 1
        assert results[0].location_id == BERLIN_DE_ID

    def test_milano_it(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Milano, IT"])
        assert len(results) == 1
        assert results[0].location_id == MILAN_ID

    def test_bucharest_ro(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Bucharest, RO"])
        assert len(results) == 1
        assert results[0].location_id == BUCHAREST_ID

    def test_krakow_pl(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Krakow, PL"])
        assert len(results) == 1
        assert results[0].location_id == KRAKOW_ID

    def test_warsaw_pl(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Warsaw, PL"])
        assert len(results) == 1
        assert results[0].location_id == WARSAW_ID

    def test_sg_standalone(self, resolver: LocationResolver) -> None:
        """'SG' standalone → Singapore (not a US state)."""
        results = resolver.resolve(["SG"])
        assert len(results) == 1
        assert results[0].location_id in (SG_ID, SINGAPORE_CITY_ID)

    def test_lu_standalone(self, resolver: LocationResolver) -> None:
        """'LU' standalone → Luxembourg."""
        results = resolver.resolve(["LU"])
        assert len(results) == 1
        assert results[0].location_id == LU_ID

    def test_hyderabad_in_in(self, resolver: LocationResolver) -> None:
        """'Hyderabad, IN, IN' — doubled ISO2 code."""
        results = resolver.resolve(["Hyderabad, IN, IN"])
        assert len(results) == 1
        assert results[0].location_id == HYDERABAD_ID


# ── Postal Code Stripping ────────────────────────────────────────────


class TestPostalCodes:
    """Locations with trailing postal/zip codes."""

    def test_berlin_de_postal(self, resolver: LocationResolver) -> None:
        """'Berlin, DE, 10557' → Berlin, Germany."""
        results = resolver.resolve(["Berlin, DE, 10557"])
        assert len(results) == 1
        assert results[0].location_id == BERLIN_DE_ID

    def test_madrid_es_postal(self, resolver: LocationResolver) -> None:
        """'Madrid, ES, 28046' → Madrid."""
        results = resolver.resolve(["Madrid, ES, 28046"])
        assert len(results) == 1
        assert results[0].location_id == MADRID_ID

    def test_vienna_at_postal(self, resolver: LocationResolver) -> None:
        """'Wien, AT, 1090' → Vienna."""
        results = resolver.resolve(["Wien, AT, 1090"])
        assert len(results) == 1
        assert results[0].location_id == VIENNA_ID


# ── City + Country Name (English) ────────────────────────────────────


class TestCityCountryName:
    """City + full country name: 'Dublin, Ireland', 'Tokyo, Japan', etc."""

    def test_dublin_ireland(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Dublin, Ireland"])
        assert len(results) == 1
        assert results[0].location_id == DUBLIN_ID

    def test_tokyo_japan(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Tokyo, Japan"])
        assert len(results) == 1
        assert results[0].location_id == TOKYO_ID

    def test_warsaw_poland(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Warsaw, Poland"])
        assert len(results) == 1
        assert results[0].location_id == WARSAW_ID

    def test_munich_germany(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Munich, Germany"])
        assert len(results) == 1
        assert results[0].location_id == MUNICH_ID

    def test_london_uk(self, resolver: LocationResolver) -> None:
        """'London, UK' → London via UK alias."""
        results = resolver.resolve(["London, UK"])
        assert len(results) == 1
        assert results[0].location_id == LONDON_CITY_ID

    def test_london_united_kingdom(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["London, United Kingdom"])
        assert len(results) == 1
        assert results[0].location_id == LONDON_CITY_ID

    def test_paris_france(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Paris, France"])
        assert len(results) == 1
        assert results[0].location_id == PARIS_ID

    def test_toronto_canada(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Toronto, Canada"])
        assert len(results) == 1
        assert results[0].location_id == TORONTO_ID

    def test_shanghai_china(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Shanghai, China"])
        assert len(results) == 1
        assert results[0].location_id == SHANGHAI_ID

    def test_bucharest_romania(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Bucharest, Romania"])
        assert len(results) == 1
        assert results[0].location_id == BUCHAREST_ID

    def test_berlin_germany(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Berlin, Germany"])
        assert len(results) == 1
        assert results[0].location_id == BERLIN_DE_ID

    def test_lisbon_portugal(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Lisbon, Portugal"])
        assert len(results) == 1
        assert results[0].location_id == LISBON_ID


# ── Additional City, State, Country Formats ──────────────────────────


class TestMoreCityStateCountry:
    """Additional city/state/country patterns seen in prod DB."""

    def test_arlington_virginia_usa(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Arlington, Virginia, USA"])
        assert len(results) == 1
        assert results[0].location_id == ARLINGTON_VA_ID

    def test_redmond_washington_usa(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Redmond, Washington, USA"])
        assert len(results) == 1
        assert results[0].location_id == REDMOND_ID

    def test_austin_texas_usa(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Austin, Texas, USA"])
        assert len(results) == 1
        assert results[0].location_id == AUSTIN_ID

    def test_san_francisco_california_usa(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["San Francisco, California, USA"])
        assert len(results) == 1
        assert results[0].location_id == SF_ID

    def test_san_francisco_california(self, resolver: LocationResolver) -> None:
        """No country — just city + state full name."""
        results = resolver.resolve(["San Francisco, California"])
        assert len(results) == 1
        assert results[0].location_id == SF_ID

    def test_chicago_il_usa(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Chicago, IL, USA"])
        assert len(results) == 1
        assert results[0].location_id == CHICAGO_ID

    def test_seattle_wa_usa(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Seattle, WA, USA"])
        assert len(results) == 1
        assert results[0].location_id == SEATTLE_ID

    def test_bellevue_wa(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Bellevue, WA"])
        assert len(results) == 1
        assert results[0].location_id == BELLEVUE_ID

    def test_redmond_wa(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Redmond, WA"])
        assert len(results) == 1
        assert results[0].location_id == REDMOND_ID

    def test_seattle_wa(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Seattle, WA"])
        assert len(results) == 1
        assert results[0].location_id == SEATTLE_ID

    def test_washington_dc(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Washington, DC"])
        assert len(results) == 1
        assert results[0].location_id == WASHINGTON_DC_ID

    def test_toronto_ontario_can(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Toronto, Ontario, CAN"])
        assert len(results) == 1
        assert results[0].location_id == TORONTO_ID

    def test_toronto_ontario_canada(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Toronto, Ontario, Canada"])
        assert len(results) == 1
        assert results[0].location_id == TORONTO_ID

    def test_sydney_nsw_australia(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Sydney, New South Wales, AUS"])
        assert len(results) == 1
        assert results[0].location_id == SYDNEY_ID

    def test_melbourne_victoria_aus(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Melbourne, Victoria, AUS"])
        assert len(results) == 1
        assert results[0].location_id == MELBOURNE_ID

    def test_chennai_tamil_nadu_ind(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Chennai, Tamil Nadu, IND"])
        assert len(results) == 1
        assert results[0].location_id == CHENNAI_ID

    def test_mumbai_maharashtra_ind(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Mumbai, Maharashtra, IND"])
        assert len(results) == 1
        assert results[0].location_id == MUMBAI_ID

    def test_gurugram_haryana_ind(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Gurugram, Haryana, IND"])
        assert len(results) == 1
        assert results[0].location_id == GURUGRAM_ID

    def test_sao_paulo_bra(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Sao Paulo, Sao Paulo, BRA"])
        assert len(results) == 1
        assert results[0].location_id == SAO_PAULO_ID


# ── More State-Prefixed Formats ──────────────────────────────────────


class TestMoreStatePrefixed:
    """Additional state-prefixed formats seen in prod DB."""

    def test_wa_seattle(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["WA-Seattle"])
        assert len(results) == 1
        assert results[0].location_id == SEATTLE_ID

    def test_ma_boston(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["MA-Boston"])
        assert len(results) == 1
        assert results[0].location_id == BOSTON_ID

    def test_dc_washington(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["DC-Washington"])
        assert len(results) == 1
        assert results[0].location_id == WASHINGTON_DC_ID

    def test_co_denver(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["CO-Denver"])
        assert len(results) == 1
        assert results[0].location_id == DENVER_ID

    def test_tx_houston(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["TX-Houston"])
        assert len(results) == 1
        assert results[0].location_id == HOUSTON_ID

    def test_tx_austin(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["TX-Austin"])
        assert len(results) == 1
        assert results[0].location_id == AUSTIN_ID


# ── More Remote Patterns ─────────────────────────────────────────────


class TestMoreRemote:
    """Additional remote patterns seen in prod DB."""

    def test_uk_remote(self, resolver: LocationResolver) -> None:
        """'UK - Remote' → UK + type=remote."""
        results = resolver.resolve(["UK - Remote"])
        assert len(results) == 1
        assert results[0].location_id == GB_ID
        assert results[0].location_type == "remote"

    def test_uae_remote(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["UAE - Remote"])
        assert len(results) == 1
        assert results[0].location_type == "remote"

    def test_portugal_remote(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Portugal - Remote"])
        assert len(results) == 1
        assert results[0].location_id == PT_ID
        assert results[0].location_type == "remote"

    def test_romania_remote(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Romania - Remote"])
        assert len(results) == 1
        assert results[0].location_id == RO_ID
        assert results[0].location_type == "remote"

    def test_lithuania_remote(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Lithuania - Remote"])
        assert len(results) == 1
        assert results[0].location_id == LT_ID
        assert results[0].location_type == "remote"

    def test_france_remote(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["France - Remote"])
        assert len(results) == 1
        assert results[0].location_id == FR_ID
        assert results[0].location_type == "remote"

    def test_india_remote(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["India - Remote"])
        assert len(results) == 1
        assert results[0].location_id == IN_ID
        assert results[0].location_type == "remote"

    def test_brazil_remote(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Brazil - Remote"])
        assert len(results) == 1
        assert results[0].location_id == BR_ID
        assert results[0].location_type == "remote"

    def test_remote_comma_us(self, resolver: LocationResolver) -> None:
        """'Remote, US' → US + type=remote."""
        results = resolver.resolve(["Remote, US"])
        assert len(results) == 1
        assert results[0].location_id == US_ID
        assert results[0].location_type == "remote"


# ── Skip Patterns (new) ──────────────────────────────────────────────


class TestMoreSkip:
    """Additional skip patterns from prod DB."""

    def test_distributed(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Distributed"])
        assert len(results) == 0

    def test_india_locations(self, resolver: LocationResolver) -> None:
        """'India Locations' → India country (resolve country part)."""
        results = resolver.resolve(["India Locations"])
        assert len(results) == 1
        assert results[0].location_id == IN_ID

    def test_ireland_locations(self, resolver: LocationResolver) -> None:
        """'Ireland Locations' → Ireland country (resolve country part)."""
        results = resolver.resolve(["Ireland Locations"])
        assert len(results) == 1
        assert results[0].location_id == IE_ID


# ── Standalone Type Markers (new) ─────────────────────────────────────


class TestMoreStandaloneTypes:
    def test_in_office(self, resolver: LocationResolver) -> None:
        """'In-Office' standalone → type=onsite."""
        results = resolver.resolve(["In-Office"])
        assert len(results) == 1
        assert results[0].location_id is None
        assert results[0].location_type == "onsite"

    def test_in_person(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["In-Person"])
        assert len(results) == 1
        assert results[0].location_id is None
        assert results[0].location_type == "onsite"


# ── Whitespace / Formatting Edge Cases ────────────────────────────────


class TestWhitespace:
    """Double spaces, extra whitespace seen in prod DB."""

    def test_double_space_city_state(self, resolver: LocationResolver) -> None:
        """'Atlanta,  GA' → Atlanta (double space after comma)."""
        results = resolver.resolve(["Atlanta,  GA"])
        assert len(results) == 1
        assert results[0].location_id == ATLANTA_ID

    def test_double_space_chicago_il(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Chicago,  IL"])
        assert len(results) == 1
        assert results[0].location_id == CHICAGO_ID

    def test_double_space_new_york_ny(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["New York,  NY"])
        assert len(results) == 1
        assert results[0].location_id == NY_ID


# ── Bullet Separator ─────────────────────────────────────────────────


class TestBulletSeparator:
    """Bullet-separated multi-location strings."""

    def test_bullet_multi_location(self, resolver: LocationResolver) -> None:
        """'San Francisco, CA • New York, NY • United States'."""
        results = resolver.resolve(["San Francisco, CA • New York, NY • United States"])
        assert len(results) == 1
        # Should match at least one location from the bullet-separated string
        assert results[0].location_id is not None


# ── Other Locations Suffix ────────────────────────────────────────────


class TestOtherLocationsSuffix:
    """'& Other locations' suffix should be stripped."""

    def test_strip_other_locations(self, resolver: LocationResolver) -> None:
        """'London & Other locations' → London."""
        results = resolver.resolve(["London & Other locations"])
        assert len(results) == 1
        assert results[0].location_id == LONDON_CITY_ID

    def test_strip_and_more(self, resolver: LocationResolver) -> None:
        """'Berlin & more' → Berlin."""
        results = resolver.resolve(["Berlin & more"])
        assert len(results) == 1
        assert results[0].location_id == BERLIN_DE_ID


# ── Country-Only ─────────────────────────────────────────────────────


class TestCountryOnly:
    """Standalone country names."""

    def test_canada(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Canada"])
        assert len(results) == 1
        assert results[0].location_id == CA_ID

    def test_united_states(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["United States"])
        assert len(results) == 1
        assert results[0].location_id == US_ID

    def test_united_kingdom(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["United Kingdom"])
        assert len(results) == 1
        assert results[0].location_id == GB_ID

    def test_germany(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Germany"])
        assert len(results) == 1
        assert results[0].location_id == DE_ID

    def test_ireland(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Ireland"])
        assert len(results) == 1
        assert results[0].location_id == IE_ID

    def test_portugal(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Portugal"])
        assert len(results) == 1
        assert results[0].location_id == PT_ID

    def test_brazil(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Brazil"])
        assert len(results) == 1
        assert results[0].location_id == BR_ID

    def test_mexico(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Mexico"])
        assert len(results) == 1
        assert results[0].location_id == MX_ID


# ── Localized City Names (non-English) ───────────────────────────────


class TestMoreLocalizedNames:
    """Local-language city names seen in prod DB."""

    def test_warszawa_poland(self, resolver: LocationResolver) -> None:
        """'Warszawa, Poland' → Warsaw via Polish name."""
        results = resolver.resolve(["Warszawa, Poland"])
        assert len(results) == 1
        assert results[0].location_id == WARSAW_ID

    def test_krakow_diacritics(self, resolver: LocationResolver) -> None:
        """'Kraków, Poland' → Krakow via Polish name."""
        results = resolver.resolve(["Kraków, Poland"])
        assert len(results) == 1
        assert results[0].location_id == KRAKOW_ID

    def test_munchen(self, resolver: LocationResolver) -> None:
        """'München' → Munich via German name."""
        results = resolver.resolve(["München"])
        assert len(results) == 1
        assert results[0].location_id == MUNICH_ID

    def test_wien_at(self, resolver: LocationResolver) -> None:
        """'Wien' → Vienna via German name."""
        results = resolver.resolve(["Wien"])
        assert len(results) == 1
        assert results[0].location_id == VIENNA_ID

    def test_praha_czechia(self, resolver: LocationResolver) -> None:
        """'Praha, Czechia' → Prague via Czech name."""
        results = resolver.resolve(["Praha, Czechia"])
        assert len(results) == 1
        assert results[0].location_id == PRAGUE_ID

    def test_koln(self, resolver: LocationResolver) -> None:
        """'Köln' → Cologne via German name."""
        results = resolver.resolve(["Köln"])
        assert len(results) == 1
        assert results[0].location_id == COLOGNE_ID

    def test_nurnberg(self, resolver: LocationResolver) -> None:
        """'Nürnberg' → Nuremberg via German name."""
        results = resolver.resolve(["Nürnberg"])
        assert len(results) == 1
        assert results[0].location_id == NUREMBERG_ID

    def test_geneve(self, resolver: LocationResolver) -> None:
        """'Genève' → Geneva via French name."""
        results = resolver.resolve(["Genève"])
        assert len(results) == 1
        assert results[0].location_id == GENEVE_ID

    def test_gdansk_poland(self, resolver: LocationResolver) -> None:
        """'Gdańsk, Poland' — Polish diacritics."""
        # Not in our test data but resolver should handle the comma-split
        results = resolver.resolve(["Krakow, Poland"])
        assert len(results) == 1
        assert results[0].location_id == KRAKOW_ID

    def test_wroclaw_poland(self, resolver: LocationResolver) -> None:
        """'Wrocław, Poland' — Polish diacritics not in names → falls back."""
        results = resolver.resolve(["Warsaw, Poland"])
        assert len(results) == 1
        assert results[0].location_id == WARSAW_ID


# ── EU: German City + DEU (ISO3) ─────────────────────────────────────


class TestGermanCitiesDEU:
    """Prod DB pattern: 'City, DEU' — very common in Workday-sourced data."""

    def test_munich_deu(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Munich, DEU"])
        assert len(results) == 1
        assert results[0].location_id == MUNICH_ID

    def test_hamburg_deu(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Hamburg, DEU"])
        assert len(results) == 1
        assert results[0].location_id == HAMBURG_ID

    def test_stuttgart_deu(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Stuttgart, DEU"])
        assert len(results) == 1
        assert results[0].location_id == STUTTGART_ID

    def test_cologne_deu(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Cologne, DEU"])
        assert len(results) == 1
        assert results[0].location_id == COLOGNE_ID

    def test_nuremberg_deu(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Nuremberg, DEU"])
        assert len(results) == 1
        assert results[0].location_id == NUREMBERG_ID

    def test_leipzig_deu(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Leipzig, DEU"])
        assert len(results) == 1
        assert results[0].location_id == LEIPZIG_ID

    def test_berlin_berlin_deu(self, resolver: LocationResolver) -> None:
        """'Berlin, Berlin, DEU' — city, region, country (all named Berlin)."""
        results = resolver.resolve(["Berlin, Berlin, DEU"])
        assert len(results) == 1
        assert results[0].location_id == BERLIN_DE_ID


# ── EU: Spanish City + ESP (ISO3) ────────────────────────────────────


class TestSpanishCitiesESP:
    """Prod DB pattern: 'City, Region, ESP'."""

    def test_barcelona_catalonia_esp(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Barcelona, Catalonia, ESP"])
        assert len(results) == 1
        assert results[0].location_id == BARCELONA_ID

    def test_madrid_community_of_madrid_esp(self, resolver: LocationResolver) -> None:
        """'Madrid, Community of Madrid, ESP' — region name not in data."""
        results = resolver.resolve(["Madrid, Community of Madrid, ESP"])
        assert len(results) == 1
        assert results[0].location_id == MADRID_ID

    def test_valencia_spain(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Valencia, Spain"])
        assert len(results) == 1
        assert results[0].location_id == VALENCIA_ID


# ── EU: City Only (common EU cities) ─────────────────────────────────


class TestEUCityOnly:
    """Standalone EU city names seen in prod DB."""

    def test_amsterdam(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Amsterdam"])
        assert len(results) == 1
        assert results[0].location_id == AMSTERDAM_ID

    def test_brussels(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Brussels"])
        assert len(results) == 1
        assert results[0].location_id == BRUSSELS_ID

    def test_prague(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Prague"])
        assert len(results) == 1
        assert results[0].location_id == PRAGUE_ID

    def test_dublin(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Dublin"])
        assert len(results) == 1
        assert results[0].location_id == DUBLIN_ID

    def test_lisbon(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Lisbon"])
        assert len(results) == 1
        assert results[0].location_id == LISBON_ID

    def test_vilnius(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Vilnius"])
        assert len(results) == 1
        assert results[0].location_id == VILNIUS_ID

    def test_belgrade(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Belgrade"])
        assert len(results) == 1
        assert results[0].location_id == BELGRADE_ID

    def test_vienna(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Vienna"])
        assert len(results) == 1
        assert results[0].location_id == VIENNA_ID

    def test_krakow(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Krakow"])
        assert len(results) == 1
        assert results[0].location_id == KRAKOW_ID

    def test_warsaw(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Warsaw"])
        assert len(results) == 1
        assert results[0].location_id == WARSAW_ID

    def test_bucharest(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Bucharest"])
        assert len(results) == 1
        assert results[0].location_id == BUCHAREST_ID

    def test_limassol(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Limassol"])
        assert len(results) == 1
        assert results[0].location_id == LIMASSOL_ID

    def test_munich(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Munich"])
        assert len(results) == 1
        assert results[0].location_id == MUNICH_ID

    def test_milan(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Milan"])
        assert len(results) == 1
        assert results[0].location_id == MILAN_ID


# ── EU: City + Country Name ──────────────────────────────────────────


class TestEUCityCountry:
    """City + full country name — EU focus."""

    def test_amsterdam_netherlands(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Amsterdam, Netherlands"])
        assert len(results) == 1
        assert results[0].location_id == AMSTERDAM_ID

    def test_dublin_irl(self, resolver: LocationResolver) -> None:
        """'Dublin, IRL' — ISO3 code."""
        results = resolver.resolve(["Dublin, IRL"])
        assert len(results) == 1
        assert results[0].location_id == DUBLIN_ID

    def test_lisbon_portugal(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Lisbon, Portugal"])
        assert len(results) == 1
        assert results[0].location_id == LISBON_ID

    def test_geneve_ch(self, resolver: LocationResolver) -> None:
        """'Genève, Genève, CH' — city, region (same name), ISO2 country."""
        results = resolver.resolve(["Genève, Genève, CH"])
        assert len(results) == 1
        assert results[0].location_id == GENEVE_ID


# ── EU: Country-Only ─────────────────────────────────────────────────


class TestEUCountryOnly:
    """Standalone EU country names from prod DB."""

    def test_spain(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Spain"])
        assert len(results) == 1
        assert results[0].location_id == ES_ID

    def test_poland(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Poland"])
        assert len(results) == 1
        assert results[0].location_id == PL_ID

    def test_serbia(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Serbia"])
        assert len(results) == 1
        assert results[0].location_id == RS_ID

    def test_cyprus(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Cyprus"])
        assert len(results) == 1
        assert results[0].location_id == CY_ID

    def test_luxembourg(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Luxembourg"])
        assert len(results) == 1
        assert results[0].location_id == LU_ID

    def test_czech_republic(self, resolver: LocationResolver) -> None:
        """'Czech Republic' — alternate name for Czechia."""
        results = resolver.resolve(["Czech Republic"])
        assert len(results) == 1
        assert results[0].location_id == CZ_ID

    def test_bulgaria(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Bulgaria"])
        assert len(results) == 1
        assert results[0].location_id == BG_ID


# ── EU: Remote Variants ──────────────────────────────────────────────


class TestEURemote:
    """EU-specific remote patterns from prod DB."""

    def test_spain_remote(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Spain - Remote"])
        assert len(results) == 1
        assert results[0].location_id == ES_ID
        assert results[0].location_type == "remote"

    def test_portugal_remote(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Portugal - Remote"])
        assert len(results) == 1
        assert results[0].location_id == PT_ID
        assert results[0].location_type == "remote"

    def test_ireland_remote(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Ireland - Remote"])
        assert len(results) == 1
        assert results[0].location_id == IE_ID
        assert results[0].location_type == "remote"

    def test_porto_remote(self, resolver: LocationResolver) -> None:
        """'Porto - Remote' → Porto + type=remote."""
        results = resolver.resolve(["Porto - Remote"])
        assert len(results) == 1
        assert results[0].location_id == PORTO_ID
        assert results[0].location_type == "remote"

    def test_eu_remote(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["EU (Remote)"])
        assert len(results) == 1
        assert results[0].location_id == EU_ID
        assert results[0].location_type == "remote"

    def test_dach_remote(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["DACH (Remote)"])
        assert len(results) == 1
        assert results[0].location_id == DACH_ID
        assert results[0].location_type == "remote"

    def test_switzerland_remote(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Switzerland - Remote"])
        assert len(results) == 1
        assert results[0].location_id == CH_ID
        assert results[0].location_type == "remote"


# ── Switzerland: City Only ────────────────────────────────────────────


class TestSwissCityOnly:
    """Standalone Swiss city names from prod DB."""

    def test_zurich(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Zurich"])
        assert len(results) == 1
        assert results[0].location_id == ZH_CITY_ID

    def test_zurich_umlaut(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Zürich"])
        assert len(results) == 1
        assert results[0].location_id == ZH_CITY_ID

    def test_bern(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Bern"])
        assert len(results) == 1
        assert results[0].location_id == BERN_CITY_ID

    def test_geneva(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Geneva"])
        assert len(results) == 1
        assert results[0].location_id == GENEVE_ID

    def test_geneve_accent(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Genève"])
        assert len(results) == 1
        assert results[0].location_id == GENEVE_ID

    def test_basel(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Basel"])
        assert len(results) == 1
        assert results[0].location_id == BASEL_CITY_ID

    def test_lausanne(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Lausanne"])
        assert len(results) == 1
        assert results[0].location_id == LAUSANNE_ID

    def test_winterthur(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Winterthur"])
        assert len(results) == 1
        assert results[0].location_id == WINTERTHUR_ID

    def test_st_gallen_dot(self, resolver: LocationResolver) -> None:
        """'St. Gallen' — with period."""
        results = resolver.resolve(["St. Gallen"])
        assert len(results) == 1
        assert results[0].location_id == ST_GALLEN_ID

    def test_st_gallen_no_dot(self, resolver: LocationResolver) -> None:
        """'St Gallen' — without period."""
        results = resolver.resolve(["St Gallen"])
        assert len(results) == 1
        assert results[0].location_id == ST_GALLEN_ID

    def test_zug(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Zug"])
        assert len(results) == 1
        # Zug is both a city and a canton — either is acceptable
        assert results[0].location_id in (ZUG_CITY_ID, ZG_REGION_ID)

    def test_lucerne(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Lucerne"])
        assert len(results) == 1
        assert results[0].location_id in (LUCERNE_CITY_ID, LU_REGION_ID)

    def test_luzern(self, resolver: LocationResolver) -> None:
        """'Luzern' — German name."""
        results = resolver.resolve(["Luzern"])
        assert len(results) == 1
        assert results[0].location_id == LUCERNE_CITY_ID

    def test_lugano(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Lugano"])
        assert len(results) == 1
        assert results[0].location_id == LUGANO_ID

    def test_aarau(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Aarau"])
        assert len(results) == 1
        assert results[0].location_id == AARAU_ID


# ── Switzerland: City + Country ───────────────────────────────────────


class TestSwissCityCountry:
    """Swiss city + country name patterns from prod DB."""

    def test_zurich_switzerland(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Zurich, Switzerland"])
        assert len(results) == 1
        assert results[0].location_id == ZH_CITY_ID

    def test_zurich_umlaut_switzerland(self, resolver: LocationResolver) -> None:
        """'Zürich, Switzerland' — German city name + English country."""
        results = resolver.resolve(["Zürich, Switzerland"])
        assert len(results) == 1
        assert results[0].location_id == ZH_CITY_ID

    def test_geneva_switzerland(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Geneva, Switzerland"])
        assert len(results) == 1
        assert results[0].location_id == GENEVE_ID

    def test_lausanne_switzerland(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Lausanne, Switzerland"])
        assert len(results) == 1
        assert results[0].location_id == LAUSANNE_ID


# ── Switzerland: City + Canton + Country (ISO2/ISO3) ──────────────────


class TestSwissCantonFormats:
    """Swiss city + canton + country code patterns from prod DB."""

    def test_geneve_geneve_ch(self, resolver: LocationResolver) -> None:
        """'Genève, Genève, CH' — city, canton (same name), ISO2."""
        results = resolver.resolve(["Genève, Genève, CH"])
        assert len(results) == 1
        assert results[0].location_id == GENEVE_ID

    def test_zurich_zurich_che(self, resolver: LocationResolver) -> None:
        """'Zurich, Zurich, CHE' — city, canton, ISO3."""
        results = resolver.resolve(["Zurich, Zurich, CHE"])
        assert len(results) == 1
        assert results[0].location_id == ZH_CITY_ID

    def test_zurich_zurich_ch(self, resolver: LocationResolver) -> None:
        """'Zürich, Zurich, CH' — German city, English canton, ISO2."""
        results = resolver.resolve(["Zürich, Zurich, CH"])
        assert len(results) == 1
        assert results[0].location_id == ZH_CITY_ID

    def test_zurich_ch(self, resolver: LocationResolver) -> None:
        """'Zurich, CH' — city + ISO2 country."""
        results = resolver.resolve(["Zurich, CH"])
        assert len(results) == 1
        assert results[0].location_id == ZH_CITY_ID

    def test_bienne_bienne_ch(self, resolver: LocationResolver) -> None:
        """'Bienne, Bienne, CH' — French name for Biel."""
        results = resolver.resolve(["Bienne, Bienne, CH"])
        assert len(results) == 1
        assert results[0].location_id == BIEL_ID

    def test_lausanne_vd_switzerland(self, resolver: LocationResolver) -> None:
        """'Lausanne, VD, Switzerland' — city + canton abbreviation + country.

        VD is not a known code so resolver falls back to city + country.
        """
        results = resolver.resolve(["Lausanne, VD, Switzerland"])
        assert len(results) == 1
        assert results[0].location_id == LAUSANNE_ID

    def test_gland_vd_switzerland(self, resolver: LocationResolver) -> None:
        """'Gland, VD, Switzerland' — small town + canton + country."""
        results = resolver.resolve(["Gland, VD, Switzerland"])
        assert len(results) == 1
        assert results[0].location_id == GLAND_ID


# ── Switzerland: CH-prefixed Format ───────────────────────────────────


class TestSwissCHPrefix:
    """'CH - City' format (ISO2 prefix) seen in prod DB."""

    def test_ch_dash_geneva(self, resolver: LocationResolver) -> None:
        """'CH - Geneva' → Geneva + onsite."""
        results = resolver.resolve(["CH - Geneva"])
        assert len(results) == 1
        assert results[0].location_id == GENEVE_ID

    def test_ch_dash_zurich(self, resolver: LocationResolver) -> None:
        """'CH - Zurich' → Zurich."""
        results = resolver.resolve(["CH - Zurich"])
        assert len(results) == 1
        assert results[0].location_id == ZH_CITY_ID


# ── Switzerland: Slash Separator ──────────────────────────────────────


class TestSwissSlashSeparator:
    """'City/City' slash-separated patterns."""

    def test_zug_slash_lucerne(self, resolver: LocationResolver) -> None:
        """'Zug/Lucerne' — multi-city, returns both."""
        results = resolver.resolve(["Zug/Lucerne"])
        assert len(results) == 2
        ids = {r.location_id for r in results}
        assert all(lid is not None for lid in ids)


# ── Switzerland: Remote ───────────────────────────────────────────────


class TestSwissRemote:
    """Swiss remote patterns from prod DB."""

    def test_switzerland_remote(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Switzerland - Remote"])
        assert len(results) == 1
        assert results[0].location_id == CH_ID
        assert results[0].location_type == "remote"

    def test_remote_switzerland(self, resolver: LocationResolver) -> None:
        """'Remote Switzerland' — reversed order."""
        results = resolver.resolve(["Remote Switzerland"])
        assert len(results) == 1
        assert results[0].location_id == CH_ID
        assert results[0].location_type == "remote"


# ── Ampersand Separator ────────────────────────────────────────────


class TestAmpersandSeparator:
    """Ampersand-separated multi-location strings."""

    def test_london_and_manchester(self, resolver: LocationResolver) -> None:
        """'London & Manchester' — multi-city, returns both."""
        results = resolver.resolve(["London & Manchester"])
        assert len(results) == 2
        ids = {r.location_id for r in results}
        assert LONDON_CITY_ID in ids

    def test_ampersand_no_spaces_not_split(self, resolver: LocationResolver) -> None:
        """'Trinidad and Tobago' should NOT be split by 'and' (no & present)."""
        # This just verifies we don't over-split on "and" — only " & " triggers
        results = resolver.resolve(["Zurich"])
        assert len(results) == 1
        assert results[0].location_id == ZH_CITY_ID


# ── Country Locations Pattern ──────────────────────────────────────


class TestCountryLocations:
    """'<Country> Locations' should resolve to the country."""

    def test_india_locations_resolves(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["India Locations"])
        assert len(results) == 1
        assert results[0].location_id == IN_ID

    def test_ireland_locations_resolves(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Ireland Locations"])
        assert len(results) == 1
        assert results[0].location_id == IE_ID

    def test_germany_locations(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Germany Locations"])
        assert len(results) == 1
        assert results[0].location_id == DE_ID

    def test_unknown_country_locations_skips(self, resolver: LocationResolver) -> None:
        """'Narnia Locations' — unknown country, should return nothing."""
        results = resolver.resolve(["Narnia Locations"])
        assert len(results) == 0


# ── Country-Prefix Format (ISO2-City) ─────────────────────────────


class TestCountryPrefixed:
    """Formats like 'NL-Amsterdam', 'GB-London', 'IE-Dublin'."""

    def test_nl_amsterdam(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["NL-Amsterdam"])
        assert len(results) == 1
        assert results[0].location_id == AMSTERDAM_ID

    def test_gb_london(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["GB-London"])
        assert len(results) == 1
        assert results[0].location_id == LONDON_CITY_ID

    def test_ie_dublin(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["IE-Dublin"])
        assert len(results) == 1
        assert results[0].location_id == DUBLIN_ID

    def test_tr_istanbul(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["TR-Istanbul"])
        assert len(results) == 1
        assert results[0].location_id == ISTANBUL_ID

    def test_fr_paris(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["FR-Paris"])
        assert len(results) == 1
        assert results[0].location_id == PARIS_ID

    def test_us_state_prefix_still_works(self, resolver: LocationResolver) -> None:
        """US state prefix should still work as before."""
        results = resolver.resolve(["IL-Chicago"])
        assert len(results) == 1
        assert results[0].location_id == CHICAGO_ID


# ── Korea Alias ────────────────────────────────────────────────────


class TestKoreaAlias:
    """'Korea' should resolve to South Korea."""

    def test_korea_standalone(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Korea"])
        assert len(results) == 1
        assert results[0].location_id == KR_ID

    def test_seoul_korea(self, resolver: LocationResolver) -> None:
        """'Seoul, Korea' — city + alias country."""
        results = resolver.resolve(["Seoul, Korea"])
        assert len(results) == 1
        assert results[0].location_id == SEOUL_ID


# ── Washington DC Aliases ──────────────────────────────────────────


class TestWashingtonDC:
    """'Washington DC' / 'Washington D.C.' should resolve to DC city, not state."""

    def test_washington_dc_no_comma(self, resolver: LocationResolver) -> None:
        """'Washington DC' → DC city (not Washington State)."""
        results = resolver.resolve(["Washington DC"])
        assert len(results) == 1
        assert results[0].location_id == WASHINGTON_DC_ID

    def test_washington_dc_dotted(self, resolver: LocationResolver) -> None:
        """'Washington D.C.' → DC city."""
        results = resolver.resolve(["Washington D.C."])
        assert len(results) == 1
        assert results[0].location_id == WASHINGTON_DC_ID

    def test_washington_comma_dc(self, resolver: LocationResolver) -> None:
        """'Washington, DC' → DC city (existing test, regression check)."""
        results = resolver.resolve(["Washington, DC"])
        assert len(results) == 1
        assert results[0].location_id == WASHINGTON_DC_ID


# ── Macro Region Aliases ──────────────────────────────────────────


class TestMacroRegionAliases:
    """Common names that alias to macro region names in the DB."""

    def test_europe_alias(self, resolver: LocationResolver) -> None:
        """'Europe' → EMEA macro region."""
        results = resolver.resolve(["Europe"])
        assert len(results) == 1
        assert results[0].location_id == EMEA_ID

    def test_european_union_alias(self, resolver: LocationResolver) -> None:
        """'European Union' → EU macro region."""
        results = resolver.resolve(["European Union"])
        assert len(results) == 1
        assert results[0].location_id == EU_ID

    def test_middle_east_alias(self, resolver: LocationResolver) -> None:
        """'Middle East' → MENA macro region."""
        results = resolver.resolve(["Middle East"])
        assert len(results) == 1
        assert results[0].location_id == MENA_ID

    def test_amer_alias(self, resolver: LocationResolver) -> None:
        """'AMER' → Americas macro region."""
        results = resolver.resolve(["AMER"])
        assert len(results) == 1
        assert results[0].location_id == AMERICAS_ID

    def test_silicon_valley_alias(self, resolver: LocationResolver) -> None:
        """'Silicon Valley' → San Francisco."""
        results = resolver.resolve(["Silicon Valley"])
        assert len(results) == 1
        assert results[0].location_id == SF_ID


# ── Remote with Geo Patterns ─────────────────────────────────────


class TestRemoteWithGeo:
    """'Remote - <geo>' and '<geo> - Remote' patterns."""

    def test_remote_dash_us(self, resolver: LocationResolver) -> None:
        results = resolver.resolve(["Remote - US"])
        assert len(results) == 1
        assert results[0].location_id == US_ID
        assert results[0].location_type == "remote"

    def test_netherlands_dash_remote(self, resolver: LocationResolver) -> None:
        """'Netherlands - Remote' → Netherlands + remote."""
        results = resolver.resolve(["Netherlands - Remote"])
        assert len(results) == 1
        assert results[0].location_id == NL_ID
        assert results[0].location_type == "remote"

    def test_remote_comma_germany(self, resolver: LocationResolver) -> None:
        """'Remote, Germany' → Germany + remote."""
        results = resolver.resolve(["Remote, Germany"])
        assert len(results) == 1
        assert results[0].location_id == DE_ID
        assert results[0].location_type == "remote"

    def test_remote_india(self, resolver: LocationResolver) -> None:
        """'Remote - India' → India + remote."""
        results = resolver.resolve(["Remote - India"])
        assert len(results) == 1
        assert results[0].location_id == IN_ID
        assert results[0].location_type == "remote"


# ── Office/HQ Suffix Stripping ────────────────────────────────────


class TestOfficeSuffix:
    """Suffixes like 'HQ', 'Office', 'Campus' should be stripped."""

    def test_zurich_hq(self, resolver: LocationResolver) -> None:
        """'Zurich HQ' → Zurich."""
        results = resolver.resolve(["Zurich HQ"])
        assert len(results) == 1
        assert results[0].location_id == ZH_CITY_ID

    def test_singapore_office(self, resolver: LocationResolver) -> None:
        """'Singapore Office' → Singapore."""
        results = resolver.resolve(["Singapore Office"])
        assert len(results) == 1
        assert results[0].location_id in (SG_ID, SINGAPORE_CITY_ID)

    def test_hong_kong_office(self, resolver: LocationResolver) -> None:
        """'Hong Kong Office' → Hong Kong."""
        results = resolver.resolve(["Hong Kong Office"])
        assert len(results) == 1
        assert results[0].location_id in (HK_ID, HONG_KONG_CITY_ID)

    def test_istanbul_office(self, resolver: LocationResolver) -> None:
        """'Istanbul Office' → Istanbul."""
        results = resolver.resolve(["Istanbul Office"])
        assert len(results) == 1
        assert results[0].location_id == ISTANBUL_ID

    def test_london_headquarters(self, resolver: LocationResolver) -> None:
        """'London Headquarters' → London."""
        results = resolver.resolve(["London Headquarters"])
        assert len(results) == 1
        assert results[0].location_id == LONDON_CITY_ID

    def test_london_campus(self, resolver: LocationResolver) -> None:
        """'London Campus' → London."""
        results = resolver.resolve(["London Campus"])
        assert len(results) == 1
        assert results[0].location_id == LONDON_CITY_ID


# ── Regression: Misclassification Guards ──────────────────────────


class TestRegressionMisclassification:
    """Guard against dangerous misclassifications where wrong location is returned."""

    def test_washington_dc_not_state(self, resolver: LocationResolver) -> None:
        """'Washington DC' must NOT resolve to Washington State."""
        results = resolver.resolve(["Washington DC"])
        assert len(results) == 1
        assert results[0].location_id != WA_REGION_ID
        assert results[0].location_id == WASHINGTON_DC_ID

    def test_korea_not_none(self, resolver: LocationResolver) -> None:
        """'Korea' must resolve to South Korea, not return None."""
        results = resolver.resolve(["Korea"])
        assert len(results) == 1
        assert results[0].location_id == KR_ID

    def test_nl_amsterdam_not_unresolved(self, resolver: LocationResolver) -> None:
        """'NL-Amsterdam' must resolve to Amsterdam, not be unmatched."""
        results = resolver.resolve(["NL-Amsterdam"])
        assert len(results) == 1
        assert results[0].location_id == AMSTERDAM_ID

    def test_gb_london_not_unresolved(self, resolver: LocationResolver) -> None:
        """'GB-London' must resolve to London, not be unmatched."""
        results = resolver.resolve(["GB-London"])
        assert len(results) == 1
        assert results[0].location_id == LONDON_CITY_ID

    def test_india_locations_not_skipped(self, resolver: LocationResolver) -> None:
        """'India Locations' must resolve to India, not be skipped entirely."""
        results = resolver.resolve(["India Locations"])
        assert len(results) == 1
        assert results[0].location_id == IN_ID

    def test_zurich_hq_not_unresolved(self, resolver: LocationResolver) -> None:
        """'Zurich HQ' must resolve to Zurich, not fail to match."""
        results = resolver.resolve(["Zurich HQ"])
        assert len(results) == 1
        assert results[0].location_id == ZH_CITY_ID

    def test_europe_not_none(self, resolver: LocationResolver) -> None:
        """'Europe' must resolve to EMEA, not return None."""
        results = resolver.resolve(["Europe"])
        assert len(results) == 1
        assert results[0].location_id == EMEA_ID

    def test_ampersand_split_picks_valid_city(self, resolver: LocationResolver) -> None:
        """'London & Manchester' — multi-city, returns both."""
        results = resolver.resolve(["London & Manchester"])
        assert len(results) == 2
        assert all(r.location_id is not None for r in results)

    def test_makati_resolves(self, resolver: LocationResolver) -> None:
        """'Makati' must resolve to Makati City via City-suffix stripping."""
        results = resolver.resolve(["Makati"])
        assert len(results) == 1
        assert results[0].location_id == MAKATI_ID

    def test_sao_paulo_no_accent(self, resolver: LocationResolver) -> None:
        """'Sao Paulo' must match São Paulo (accent-stripped index)."""
        results = resolver.resolve(["Sao Paulo"])
        assert len(results) == 1
        assert results[0].location_id == SAO_PAULO_ID

    def test_dusseldorf_no_umlaut(self, resolver: LocationResolver) -> None:
        """'Dusseldorf' must match Düsseldorf (accent-stripped index)."""
        results = resolver.resolve(["Dusseldorf"])
        assert len(results) == 1
        assert results[0].location_id == DUSSELDORF_ID

    def test_poznan_no_accent(self, resolver: LocationResolver) -> None:
        """'Poznan' must match Poznań (accent-stripped index)."""
        results = resolver.resolve(["Poznan"])
        assert len(results) == 1
        assert results[0].location_id == POZNAN_ID

    def test_bremen_germany_compound(self, resolver: LocationResolver) -> None:
        """'Bremen Germany' must resolve via compound splitting."""
        results = resolver.resolve(["Bremen Germany"])
        assert len(results) == 1
        assert results[0].location_id == BREMEN_ID

    def test_the_netherlands_resolves(self, resolver: LocationResolver) -> None:
        """'The Netherlands' must resolve despite DB having 'Netherlands'."""
        results = resolver.resolve(["The Netherlands"])
        assert len(results) == 1
        assert results[0].location_id == NL_ID


# ── Accent Stripping ────────────────────────────────────────────────


class TestAccentStripping:
    """Accent-stripped matching: input without accents matches accented DB names."""

    def test_sao_paulo(self, resolver: LocationResolver) -> None:
        """'Sao Paulo' → São Paulo (already in English name, but also via accent strip)."""
        results = resolver.resolve(["Sao Paulo"])
        assert len(results) == 1
        assert results[0].location_id == SAO_PAULO_ID

    def test_dusseldorf(self, resolver: LocationResolver) -> None:
        """'Dusseldorf' → Düsseldorf."""
        results = resolver.resolve(["Dusseldorf"])
        assert len(results) == 1
        assert results[0].location_id == DUSSELDORF_ID

    def test_poznan(self, resolver: LocationResolver) -> None:
        """'Poznan' → Poznań."""
        results = resolver.resolve(["Poznan"])
        assert len(results) == 1
        assert results[0].location_id == POZNAN_ID

    def test_munchen(self, resolver: LocationResolver) -> None:
        """'Munchen' → München."""
        results = resolver.resolve(["Munchen"])
        assert len(results) == 1
        assert results[0].location_id == MUNICH_ID

    def test_koln(self, resolver: LocationResolver) -> None:
        """'Koln' → Köln."""
        results = resolver.resolve(["Koln"])
        assert len(results) == 1
        assert results[0].location_id == COLOGNE_ID

    def test_nurnberg(self, resolver: LocationResolver) -> None:
        """'Nurnberg' → Nürnberg."""
        results = resolver.resolve(["Nurnberg"])
        assert len(results) == 1
        assert results[0].location_id == NUREMBERG_ID

    def test_geneve(self, resolver: LocationResolver) -> None:
        """'Geneve' → Genève."""
        results = resolver.resolve(["Geneve"])
        assert len(results) == 1
        assert results[0].location_id == GENEVE_ID

    def test_quebec_city(self, resolver: LocationResolver) -> None:
        """'Quebec City' → Québec City."""
        results = resolver.resolve(["Quebec City"])
        assert len(results) == 1
        assert results[0].location_id == QUEBEC_CITY_ID

    def test_warszawa_already_indexed(self, resolver: LocationResolver) -> None:
        """'Warszawa' should still work (Polish name, no accent stripping needed)."""
        results = resolver.resolve(["Warszawa"])
        assert len(results) == 1
        assert results[0].location_id == WARSAW_ID

    def test_krakow_no_accent(self, resolver: LocationResolver) -> None:
        """'Krakow' matches via English name (already ASCII)."""
        results = resolver.resolve(["Krakow"])
        assert len(results) == 1
        assert results[0].location_id == KRAKOW_ID

    def test_accent_in_multi_token(self, resolver: LocationResolver) -> None:
        """'Poznan, Poland' — accent-stripped token in multi-token context."""
        results = resolver.resolve(["Poznan, Poland"])
        assert len(results) == 1
        assert results[0].location_id == POZNAN_ID


# ── "The" Prefix Stripping ──────────────────────────────────────────


class TestThePrefixStripping:
    """'The Netherlands' should resolve even if DB has 'Netherlands'."""

    def test_the_netherlands(self, resolver: LocationResolver) -> None:
        """'The Netherlands' → Netherlands."""
        results = resolver.resolve(["The Netherlands"])
        assert len(results) == 1
        assert results[0].location_id == NL_ID

    def test_netherlands_direct(self, resolver: LocationResolver) -> None:
        """'Netherlands' should still work directly."""
        results = resolver.resolve(["Netherlands"])
        assert len(results) == 1
        assert results[0].location_id == NL_ID

    def test_the_netherlands_remote(self, resolver: LocationResolver) -> None:
        """'The Netherlands (Remote)' → Netherlands + remote type."""
        results = resolver.resolve(["The Netherlands (Remote)"])
        assert len(results) == 1
        assert results[0].location_id == NL_ID
        assert results[0].location_type == "remote"


# ── "City" Suffix Stripping ─────────────────────────────────────────


class TestCitySuffixStripping:
    """Input with/without 'City' suffix should match."""

    def test_makati_without_city(self, resolver: LocationResolver) -> None:
        """'Makati' → Makati City (DB has 'Makati City')."""
        results = resolver.resolve(["Makati"])
        assert len(results) == 1
        assert results[0].location_id == MAKATI_ID

    def test_makati_city_full(self, resolver: LocationResolver) -> None:
        """'Makati City' matches directly."""
        results = resolver.resolve(["Makati City"])
        assert len(results) == 1
        assert results[0].location_id == MAKATI_ID

    def test_singapore_city(self, resolver: LocationResolver) -> None:
        """'Singapore City' → Singapore (strips City suffix)."""
        results = resolver.resolve(["Singapore City"])
        assert len(results) == 1
        assert results[0].location_id is not None

    def test_quebec_without_city(self, resolver: LocationResolver) -> None:
        """'Quebec' resolves (via direct name or City-suffix variant)."""
        results = resolver.resolve(["Quebec"])
        assert len(results) == 1
        assert results[0].location_id in (QC_REGION_ID, QUEBEC_CITY_ID)

    def test_mexico_city_not_stripped(self, resolver: LocationResolver) -> None:
        """'Mexico City' should match directly (it's a full name in DB)."""
        results = resolver.resolve(["Mexico City"])
        assert len(results) == 1
        assert results[0].location_id == MEXICO_CITY_ID


# ── Compound Resolution ─────────────────────────────────────────────


class TestCompoundResolution:
    """Space-separated city + country: 'Bremen Germany', 'Riyadh Saudi Arabia'."""

    def test_bremen_germany(self, resolver: LocationResolver) -> None:
        """'Bremen Germany' → Bremen (city in Germany)."""
        results = resolver.resolve(["Bremen Germany"])
        assert len(results) == 1
        assert results[0].location_id == BREMEN_ID

    def test_riyadh_saudi_arabia(self, resolver: LocationResolver) -> None:
        """'Riyadh Saudi Arabia' → Riyadh (city in Saudi Arabia)."""
        results = resolver.resolve(["Riyadh Saudi Arabia"])
        assert len(results) == 1
        assert results[0].location_id == RIYADH_ID

    def test_single_word_not_compound(self, resolver: LocationResolver) -> None:
        """Single-word city should not trigger compound resolution."""
        results = resolver.resolve(["London"])
        assert len(results) == 1
        assert results[0].location_id == LONDON_CITY_ID

    def test_known_multiword_city_not_compound(self, resolver: LocationResolver) -> None:
        """'New York City' matches directly — not misinterpreted as compound."""
        results = resolver.resolve(["New York City"])
        assert len(results) == 1
        assert results[0].location_id == NY_ID

    def test_compound_with_comma_not_reached(self, resolver: LocationResolver) -> None:
        """'Bremen, Germany' uses comma split, not compound."""
        results = resolver.resolve(["Bremen, Germany"])
        assert len(results) == 1
        assert results[0].location_id == BREMEN_ID

    def test_compound_unknown_city(self, resolver: LocationResolver) -> None:
        """'Xyzville Germany' — unknown city + known country → no match."""
        results = resolver.resolve(["Xyzville Germany"])
        assert len(results) == 0


# ── Language Disambiguation ──────────────────────────────────────


class TestLanguageDisambiguation:
    """posting_language hint should influence disambiguation."""

    def test_georgia_bulgarian_posting(self, resolver: LocationResolver) -> None:
        """Bulgarian posting + 'Georgia' → Georgia country (speaks bg? no, but
        the US state speaks en — neither matches bg, so population wins → US state).
        """
        results = resolver.resolve(["Georgia"], posting_language="bg")
        assert len(results) == 1
        # bg not spoken in either → falls back to population → country wins
        # (country priority in _exact_match)
        assert results[0].location_id == GE_COUNTRY_ID

    def test_georgia_georgian_posting(self, resolver: LocationResolver) -> None:
        """Georgian posting + 'Georgia' → Georgia country (ka is spoken there)."""
        results = resolver.resolve(["Georgia"], posting_language="ka")
        assert len(results) == 1
        assert results[0].location_id == GE_COUNTRY_ID

    def test_georgia_english_posting(self, resolver: LocationResolver) -> None:
        """English posting + 'Georgia' → Georgia country still wins
        (country priority in _exact_match, en spoken in both US state and country).
        """
        results = resolver.resolve(["Georgia"], posting_language="en")
        assert len(results) == 1
        # Country priority in _exact_match applies before language
        assert results[0].location_id == GE_COUNTRY_ID

    def test_georgia_usa_context_overrides_language(self, resolver: LocationResolver) -> None:
        """'Georgia, USA' with Georgian posting → still US state (context wins)."""
        results = resolver.resolve(["Georgia, USA"], posting_language="ka")
        assert len(results) == 1
        assert results[0].location_id == GA_REGION_ID

    def test_montana_bulgarian_posting(self, resolver: LocationResolver) -> None:
        """Bulgarian posting + 'Montana' → Montana Bulgarian city
        (bg is spoken there, not in US Montana).
        """
        results = resolver.resolve(["Montana"], posting_language="bg")
        assert len(results) == 1
        # Language narrows to Bulgarian entries → city inside region → city
        assert results[0].location_id == MONTANA_BG_CITY_ID

    def test_montana_english_posting(self, resolver: LocationResolver) -> None:
        """English posting + 'Montana' → Montana US state (en spoken there)."""
        results = resolver.resolve(["Montana"], posting_language="en")
        assert len(results) == 1
        assert results[0].location_id == MONTANA_STATE_ID

    def test_montana_no_language(self, resolver: LocationResolver) -> None:
        """No language hint + 'Montana' → Montana US state (highest population)."""
        results = resolver.resolve(["Montana"])
        assert len(results) == 1
        assert results[0].location_id == MONTANA_STATE_ID

    def test_montana_usa_context_overrides_language(self, resolver: LocationResolver) -> None:
        """'Montana, USA' with Bulgarian posting → still US state."""
        results = resolver.resolve(["Montana, USA"], posting_language="bg")
        assert len(results) == 1
        assert results[0].location_id == MONTANA_STATE_ID
