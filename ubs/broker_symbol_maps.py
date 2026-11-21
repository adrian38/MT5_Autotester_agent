"""Como se llama cada producto en cada broker.

Tablas puras, sin dependencias del resto del paquete: `ubs.account` las expone
al resto del proyecto. Viven aparte porque crecen con cada cliente que importa
seeds escritas con la nomenclatura de su propio broker.
"""
from __future__ import annotations


VANTAGE_DEFAULT_SYMBOL_MAP = (
    "US30=DJ30.r,DJI=DJ30.r,US100=NAS100.r,USTEC=NAS100.r,USTECH=NAS100.r,"
    "NASDAQ=NAS100.r,US500=SP500.r,SPX500=SP500.r,DAX=GER40.r,DE40=GER40.r,"
    "CAC40=FRA40.r,FTSE=UK100.r,FT100=UK100.r,JP225=Nikkei225,JPN225=Nikkei225,"
    "NK225=Nikkei225,HSI=HK50.r,CN50=CHINA50.r,AUS200=SPI200.r,STOXX50=EU50.r,"
    "EUSTX50=EU50.r,EURO50=EU50.r,DXY=USDX.r,USDINDEX=USDX.r,"
    "WTI=USOUSD,XTIUSD=USOUSD,USOIL=USOUSD,CRUDEOIL=USOUSD,"
    "BRENT=UKOUSD,XBRUSD=UKOUSD,UKOIL=UKOUSD,"
    "GOLD=XAUUSD+,SILVER=XAGUSD,TSLA.NAS=TSLA,AAPL.NAS=AAPL"
)
ROBOFOREX_DEFAULT_SYMBOL_MAP = (
    "CRUDEOIL=WTI,XTIUSD=WTI,USTEC=.USTECHCash,US100=.USTECHCash,"
    "US30=.US30Cash,US500=.US500Cash,DAX=.DE40Cash,DE40=.DE40Cash,"
    "TSLA.NAS=TSLA,AAPL.NAS=AAPL"
)
ICTRADING_DEFAULT_SYMBOL_MAP = (
    "US100=USTEC,NAS100=USTEC,NASDAQ=USTEC,SPX500=US500,DAX=DE40,"
    "GER40=DE40,JPN225=JP225,BRENT=XBRUSD,UKOIL=XBRUSD,WTI=XTIUSD,"
    "USOIL=XTIUSD,CRUDEOIL=XTIUSD,GOLD=XAUUSD,SILVER=XAGUSD"
)

# AXI lista las acciones por nombre de empresa, no por ticker (`Tesla+`, no
# `TSLA`), asi que una seed de cliente escrita como `TSLA.NAS` o `AAPL` no se
# parece a nada del universo y acaba con un veredicto terminal de simbolo
# inexistente. Los nombres de aqui estan comprobados contra
# `assets/axi_symbol_specs.json`, que se mide conectado al servidor.
AXI_SHARE_SYMBOL_ALIASES = {
    "AAPL": "Apple+",
    "ADBE": "Adobe+",
    "AMD": "AMD+",
    "AMZN": "Amazon+",
    "AVGO": "Broadcom+",
    "BA": "Boeing+",
    "BRK.B": "Berkshire+",
    "COIN": "Coinbase+",
    "CSCO": "Cisco+",
    "CVX": "Chevron+",
    "DIS": "WaltDisney+",
    "F": "Ford+",
    "GM": "GM+",
    "GOOG": "AlphabetC+",
    "GOOGL": "AlphabetA+",
    "INTC": "Intel+",
    "JPM": "JPMorgan+",
    "MCD": "McDonalds+",
    "META": "Meta+",
    "MSFT": "Microsoft+",
    "MU": "MicronTech+",
    "NFLX": "Netflix+",
    "NKE": "Nike+",
    "NVDA": "NVIDIA+",
    "ORCL": "Oracle+",
    "PFE": "Pfizer+",
    "PLTR": "Palantir+",
    "PYPL": "PayPal+",
    "QCOM": "QUALCOMM+",
    "SBUX": "Starbucks+",
    "SHOP": "Shopify+",
    "SNAP": "Snap+",
    "TSLA": "Tesla+",
    "UBER": "UBER+",
    "V": "VISA+",
    "WMT": "Walmart+",
    "XOM": "Exxon+",
    "ZM": "Zoom+",
}
# Los clientes escriben el mismo ticker con o sin bolsa; las cuatro
# ortografias apuntan al mismo producto de AXI.
AXI_SHARE_SYMBOL_SUFFIXES = ("", ".NAS", ".NYSE", ".US")


def axi_share_symbol_map() -> str:
    """Las aliases de acciones en el formato ORIGEN=DESTINO del mapa."""
    return ",".join(
        f"{ticker}{suffix}={target}"
        for ticker, target in AXI_SHARE_SYMBOL_ALIASES.items()
        for suffix in AXI_SHARE_SYMBOL_SUFFIXES
    )


AXI_DEFAULT_SYMBOL_MAP = ",".join(
    (
        "US100=USTECH,USTEC=USTECH,NAS100=NAS100.fs,NASDAQ=USTECH,SPX500=US500,SP500=US500,"
        "DJ30=DJ30.fs,DJI=US30,DAX=GER40,DE40=GER40,CAC40=CAC40.fs,FTSE=UK100,FT100=FT100.fs,"
        "JP225=JPN225,USOIL=USOIL,XTIUSD=USOIL,WTI=WTI.fs,CRUDEOIL=USOIL,"
        "UKOIL=UKOIL,XBRUSD=UKOIL,BRENT=BRENT.fs,NATGAS=NATGAS.fs,XNGUSD=NATGAS.fs,"
        "GOLD=XAUUSD,SILVER=XAGUSD,DXY=USDINDEX.fs,USDINDEX=USDINDEX.fs,"
        "VIX=VIX.fs,STOXX50=EU50,EURO50=EU50",
        axi_share_symbol_map(),
    )
)
AXI_CASH_FUTURE_SYMBOL_FAMILIES = (
    (("DAX", "DE40", "GER40", "DAX40"), ("GER40.sa", "DAX40.fs")),
    (("US100", "USTEC", "USTECH", "NAS100", "NASDAQ"), ("USTECH.sa", "NAS100.fs")),
    (("US30", "DJ30", "DJI"), ("US30.sa", "DJ30.fs")),
    (("US500", "SP500", "SPX500", "S&P"), ("US500.sa", "S&P.fs")),
    (("UK100", "FTSE", "FT100"), ("UK100.sa", "FT100.fs")),
    (("JP225", "JPN225", "NK225"), ("JPN225.sa", "NK225.fs")),
    (("FRA40", "CAC40"), ("FRA40.sa", "CAC40.fs")),
    (("CN50", "CHINA50"), ("CN50.sa", "CHINA50.fs")),
    (("EU50", "EUSTX50", "STOXX50", "EURO50"), ("EU50.sa", "EUSTX50.fs")),
    (("HK50", "HSI"), ("HK50.sa", "HSI.fs")),
    (("AUS200", "SPI200"), ("AUS200.sa", "SPI200.fs")),
    (("USOIL", "XTIUSD", "WTI", "CRUDEOIL"), ("USOIL.sa", "WTI.fs")),
    (("UKOIL", "XBRUSD", "BRENT"), ("UKOIL.sa", "BRENT.fs")),
)
DEFAULT_SYMBOL_MAPS_BY_BROKER = {
    "VANTAGE": VANTAGE_DEFAULT_SYMBOL_MAP,
    "ROBOFOREX": ROBOFOREX_DEFAULT_SYMBOL_MAP,
    "ICTRADING": ICTRADING_DEFAULT_SYMBOL_MAP,
    "AXI": AXI_DEFAULT_SYMBOL_MAP,
}

# Suffixes that are presentation details rather than part of symbol identity.
# Keep this policy broker-scoped: another broker may legitimately use the same
# text as an exchange/product qualifier with different semantics.
#
# AXI escribe el mismo catalogo distinto en cada servidor: `.sa` en
# `Axi-US51-Live`, sin sufijo en `Axi-US50-Live` y `.s` en `Axi-US50-Demo`
# (donde hasta los futuros van en `.s`, no en `.fs`). La lista cubre los tres
# porque una memoria o un reporte pueden venir de cualquiera de ellos; cual
# aplica en un run lo decide el universo sincronizado, no esta tabla.
BROKER_IDENTITY_SUFFIXES = {
    "AXI": (".sa", ".fs", ".s", "+"),
}
