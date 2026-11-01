"""
Sector map for NSE F&O stocks, plus rough reference prices used only to seed
the demo mode. Live mode takes the stock list from Upstox's instrument file;
any symbol not listed here shows up under "Other".
"""

# symbol: (sector, approx price used for demo data only)
STOCKS = {
    # Banks
    "HDFCBANK": ("Banks", 980), "ICICIBANK": ("Banks", 1420), "SBIN": ("Banks", 840),
    "AXISBANK": ("Banks", 1150), "KOTAKBANK": ("Banks", 2050), "INDUSINDBK": ("Banks", 780),
    "BANKBARODA": ("Banks", 245), "PNB": ("Banks", 105), "CANBK": ("Banks", 112),
    "FEDERALBNK": ("Banks", 205), "IDFCFIRSTB": ("Banks", 72), "AUBANK": ("Banks", 720),
    "BANDHANBNK": ("Banks", 165), "UNIONBANK": ("Banks", 135), "INDIANB": ("Banks", 620),
    "YESBANK": ("Banks", 21),
    # Financials
    "BAJFINANCE": ("Financials", 920), "BAJAJFINSV": ("Financials", 2000), "SHRIRAMFIN": ("Financials", 640),
    "CHOLAFIN": ("Financials", 1550), "MUTHOOTFIN": ("Financials", 2600), "MANAPPURAM": ("Financials", 260),
    "LICHSGFIN": ("Financials", 590), "PFC": ("Financials", 410), "RECLTD": ("Financials", 390),
    "SBILIFE": ("Financials", 1800), "HDFCLIFE": ("Financials", 760), "ICICIPRULI": ("Financials", 640),
    "ICICIGI": ("Financials", 1950), "SBICARD": ("Financials", 880), "LTF": ("Financials", 230),
    "M&MFIN": ("Financials", 270), "JIOFIN": ("Financials", 320), "HDFCAMC": ("Financials", 5200),
    "BSE": ("Financials", 2400), "MCX": ("Financials", 7800), "CDSL": ("Financials", 1600),
    "ANGELONE": ("Financials", 2500), "IRFC": ("Financials", 130), "POLICYBZR": ("Financials", 1850),
    "PAYTM": ("Financials", 1150), "IIFL": ("Financials", 480), "LICI": ("Financials", 900),
    # IT
    "TCS": ("IT", 3100), "INFY": ("IT", 1500), "HCLTECH": ("IT", 1480), "WIPRO": ("IT", 250),
    "TECHM": ("IT", 1450), "LTIM": ("IT", 5300), "PERSISTENT": ("IT", 5600), "COFORGE": ("IT", 1700),
    "MPHASIS": ("IT", 2800), "OFSS": ("IT", 8800), "KPITTECH": ("IT", 1250), "TATAELXSI": ("IT", 5500),
    "NAUKRI": ("IT", 1350),
    # Auto
    "MARUTI": ("Auto", 15800), "M&M": ("Auto", 3500), "TATAMOTORS": ("Auto", 680), "BAJAJ-AUTO": ("Auto", 8800),
    "HEROMOTOCO": ("Auto", 5300), "EICHERMOT": ("Auto", 6900), "TVSMOTOR": ("Auto", 3400),
    "ASHOKLEY": ("Auto", 135), "BHARATFORG": ("Auto", 1250), "MOTHERSON": ("Auto", 105),
    "BOSCHLTD": ("Auto", 38000), "EXIDEIND": ("Auto", 390), "TIINDIA": ("Auto", 3000),
    "SONACOMS": ("Auto", 460),
    # Energy & Oil
    "RELIANCE": ("Energy", 1450), "ONGC": ("Energy", 245), "BPCL": ("Energy", 340), "IOC": ("Energy", 150),
    "HINDPETRO": ("Energy", 450), "GAIL": ("Energy", 180), "PETRONET": ("Energy", 285),
    "OIL": ("Energy", 430), "IGL": ("Energy", 210),
    # Power
    "NTPC": ("Power", 340), "POWERGRID": ("Power", 290), "TATAPOWER": ("Power", 395),
    "ADANIGREEN": ("Power", 1000), "ADANIENSOL": ("Power", 900), "NHPC": ("Power", 85),
    "TORNTPOWER": ("Power", 1350), "JSWENERGY": ("Power", 520), "SUZLON": ("Power", 60),
    "IREDA": ("Power", 150), "CESC": ("Power", 165),
    # Metals & Mining
    "TATASTEEL": ("Metals", 170), "JSWSTEEL": ("Metals", 1150), "HINDALCO": ("Metals", 780),
    "VEDL": ("Metals", 480), "SAIL": ("Metals", 135), "NMDC": ("Metals", 75), "JINDALSTEL": ("Metals", 1000),
    "NATIONALUM": ("Metals", 230), "HINDZINC": ("Metals", 470), "COALINDIA": ("Metals", 390),
    "APLAPOLLO": ("Metals", 1700),
    # Pharma & Healthcare
    "SUNPHARMA": ("Pharma", 1650), "DRREDDY": ("Pharma", 1250), "CIPLA": ("Pharma", 1550),
    "DIVISLAB": ("Pharma", 6200), "LUPIN": ("Pharma", 1950), "AUROPHARMA": ("Pharma", 1100),
    "ZYDUSLIFE": ("Pharma", 980), "TORNTPHARM": ("Pharma", 3500), "ALKEM": ("Pharma", 5400),
    "BIOCON": ("Pharma", 370), "GLENMARK": ("Pharma", 1900), "MANKIND": ("Pharma", 2500),
    "LAURUSLABS": ("Pharma", 850), "APOLLOHOSP": ("Pharma", 7600), "MAXHEALTH": ("Pharma", 1150),
    "FORTIS": ("Pharma", 950), "SYNGENE": ("Pharma", 650),
    # FMCG
    "HINDUNILVR": ("FMCG", 2500), "ITC": ("FMCG", 410), "NESTLEIND": ("FMCG", 1200),
    "BRITANNIA": ("FMCG", 5900), "DABUR": ("FMCG", 510), "MARICO": ("FMCG", 720),
    "GODREJCP": ("FMCG", 1200), "COLPAL": ("FMCG", 2250), "TATACONSUM": ("FMCG", 1120),
    "UNITDSPR": ("FMCG", 1350), "VBL": ("FMCG", 470), "PATANJALI": ("FMCG", 600),
    # Consumer & Retail
    "TITAN": ("Consumer", 3600), "TRENT": ("Consumer", 4700), "DMART": ("Consumer", 4200),
    "ASIANPAINT": ("Consumer", 2450), "PIDILITIND": ("Consumer", 1500), "HAVELLS": ("Consumer", 1500),
    "VOLTAS": ("Consumer", 1350), "DIXON": ("Consumer", 15500), "POLYCAB": ("Consumer", 7300),
    "KALYANKJIL": ("Consumer", 500), "JUBLFOOD": ("Consumer", 620), "ZOMATO": ("Consumer", 320),
    "ETERNAL": ("Consumer", 320), "NYKAA": ("Consumer", 240), "INDHOTEL": ("Consumer", 760),
    "CROMPTON": ("Consumer", 330), "BLUESTARCO": ("Consumer", 1800), "PAGEIND": ("Consumer", 44000),
    # Cement & Infra
    "ULTRACEMCO": ("Infra", 12200), "SHREECEM": ("Infra", 30000), "AMBUJACEM": ("Infra", 570),
    "DALBHARAT": ("Infra", 2200), "LT": ("Infra", 3800), "ADANIPORTS": ("Infra", 1450),
    "ADANIENT": ("Infra", 2400), "GMRAIRPORT": ("Infra", 90), "DLF": ("Realty", 760),
    "GODREJPROP": ("Realty", 2100), "OBEROIRLTY": ("Realty", 1650), "LODHA": ("Realty", 1200),
    "PRESTIGE": ("Realty", 1600), "PHOENIXLTD": ("Realty", 1600), "NBCC": ("Infra", 110),
    "RVNL": ("Infra", 330), "IRCTC": ("Infra", 720), "CONCOR": ("Infra", 560),
    # Capital goods & Defence
    "HAL": ("Defence", 4600), "BEL": ("Defence", 410), "BHEL": ("Capital Goods", 245),
    "SIEMENS": ("Capital Goods", 3100), "ABB": ("Capital Goods", 5200), "CGPOWER": ("Capital Goods", 720),
    "CUMMINSIND": ("Capital Goods", 3900), "BDL": ("Defence", 1550), "MAZDOCK": ("Defence", 2800),
    "SOLARINDS": ("Defence", 14000), "KEI": ("Capital Goods", 4000), "SUPREMEIND": ("Capital Goods", 4300),
    "ASTRAL": ("Capital Goods", 1450), "INOXWIND": ("Capital Goods", 150),
    # Telecom & Media
    "BHARTIARTL": ("Telecom", 1950), "IDEA": ("Telecom", 9), "INDUSTOWER": ("Telecom", 360),
    "TATACOMM": ("Telecom", 1650), "HFCL": ("Telecom", 80), "PVRINOX": ("Media", 1100),
    # Chemicals & others
    "UPL": ("Chemicals", 690), "PIIND": ("Chemicals", 3700), "SRF": ("Chemicals", 2950),
    "DEEPAKNTR": ("Chemicals", 1950), "TATACHEM": ("Chemicals", 900), "COROMANDEL": ("Chemicals", 2300),
    "PEL": ("Financials", 1150), "INDIGO": ("Aviation", 5700), "DELHIVERY": ("Logistics", 440),
}

# indices: symbol -> (display name, approx price for demo, strike step, demo lot size,
#                     Upstox spot key, weekly options?)
INDICES = {
    "NIFTY": ("Nifty 50", 25600, 50, 65, "NSE_INDEX|Nifty 50", True),
    "BANKNIFTY": ("Nifty Bank", 57500, 100, 30, "NSE_INDEX|Nifty Bank", False),
}

SECTORS = sorted({s for s, _ in STOCKS.values()} | {"Other", "Index"})


def sector_of(symbol: str) -> str:
    if symbol in INDICES:
        return "Index"
    return STOCKS.get(symbol, ("Other", 0))[0]


def demo_strike_step(price: float) -> float:
    for limit, step in ((100, 1), (250, 2.5), (500, 5), (1000, 10), (2500, 20), (5000, 50), (10000, 100)):
        if price < limit:
            return step
    return 250
