"""Ticker universe lists for the prefilter and strategy layers."""
from src.sp500_tickers import SP500_TICKERS

# Top ~150 liquid Russell 2000 names by typical dollar volume
# These are mid-cap names consistently in IWM with high liquidity
RUSSELL_2000_LIQUID: list[str] = [
    "ACLS", "ACLX", "ADMA", "AEIS", "AGIO", "AGYS", "AIN", "ALGT", "ALKS", "AMKR",
    "AMNB", "AMPH", "AMSF", "AMWD", "ANIP", "ANSS", "AON", "ARI", "AROC", "ARQT",
    "ASAN", "ASGN", "ASIX", "ASND", "ATRC", "ATRI", "ATRO", "ATSG", "AVAV", "AVNT",
    "BANF", "BANR", "BBCP", "BCAL", "BDN", "BELFB", "BFAM", "BHVN", "BKNG", "BLBD",
    "BLFS", "BMBL", "BOOT", "BOWL", "BRC", "BRKR", "BSIG", "BSRR", "BSVN", "BWFG",
    "CABO", "CADE", "CALX", "CARG", "CASH", "CASS", "CBRL", "CCOI", "CDNA", "CENTA",
    "CHCO", "CHE", "CHRD", "CLFD", "CLF", "CLFD", "CMCO", "CMDX", "CMRE", "CNOB",
    "CNX", "COHU", "COLB", "CPF", "CRAI", "CRVL", "CSGS", "CSWI", "CTRE", "CVCO",
    "CVLT", "CXM", "DAKT", "DFIN", "DKNG", "DNOW", "DORM", "DRRX", "DSSI", "DVAX",
    "DXPE", "EFC", "EFSC", "EGBN", "EGRX", "ELME", "ENVA", "EPRT", "ESGR", "ESNT",
    "ESRT", "EXP", "EZPW", "FBNC", "FBRT", "FBSS", "FCNCA", "FCPT", "FDMT", "FELE",
    "FFIN", "FISI", "FJNK", "FLNC", "FLXS", "FMBH", "FMCB", "FOLD", "FORM", "FOUR",
    "FRPH", "FRST", "FSS", "FTDR", "FTHM", "FUTU", "GABC", "GBX", "GCO", "GCPC",
    "GDEN", "GEOS", "GIII", "GLDD", "GLOW", "GMBL", "GNTY", "GOSS", "GPRO", "GRNT",
    "GRPN", "GTLS", "HAFC", "HAYN", "HCKT", "HCI", "HCSG", "HDB", "HFFG", "HFWA",
    "HIBB", "HIW", "HLIT", "HMST", "HOFT", "HOLX", "HOPE", "HOTH", "HROW", "HTBK",
    "HUBB", "HWKN", "IART", "IBCP", "IBIO", "IBOC", "ICFI", "IDCC", "IDYA", "IESC",
    "INBK", "INMD", "INSP", "IOSP", "IPIX", "IRET", "IRMD", "ISMS", "ITCI", "JACK",
]


def get_combined_universe() -> list[str]:
    """Return deduplicated combined universe of S&P 500 + liquid Russell 2000."""
    combined = list(SP500_TICKERS)
    sp500_set = set(SP500_TICKERS)
    for t in RUSSELL_2000_LIQUID:
        if t not in sp500_set:
            combined.append(t)
    return combined
