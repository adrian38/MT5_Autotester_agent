"""Canonical UBS V6 parameter contract supplied by the user.

The key order comes from ``UBS_keys_v6.xlsx`` and the template text from
``setfile_keys_v6_default.set``. Runtime generation rules remain authoritative
for rule-locked values.
"""

from __future__ import annotations


UBS_VERSION = "V6"


V6_PARAMETER_KEYS: tuple[str, ...] = (
    "URL",
    "URLB",
    "LICURL",
    "LICURLB",
    "ShowInfoPanel",
    "UpdateInfoTesting",
    "InfoPanelSizeAdjust",
    "SetFontSize",
    "UseAutoLoader",
    "Sets_Folder",
    "overrideAutoLoadLotsSettings",
    "overrideNewsFilter",
    "overrideTimeSettings",
    "overrideProp",
    "ForcePendingMax",
    "EP",
    "RF",
    "TR",
    "MTR",
    "SpreadFilter",
    "MaxSpread",
    "DistForSpreadFilter",
    "BacktestSpeed",
    "PrintLogs",
    "ForceSymbol",
    "OnlyLoaderForceSymbol",
    "setSL_TP_After_Entry",
    "Virtual_expiration",
    "useVirtualStops",
    "VirtualSL_Safety_Hardstop_dist",
    "SetVSL_to_hardSL_sec_delay",
    "Run_Strategy",
    "AllowBuyTrades",
    "AllowSellTrades",
    "ATRDefault",
    "ATR_Period",
    "ATR_Timeframe",
    "DefaultValue",
    "VolTimeframe",
    "VolCandles",
    "minSize",
    "AtrPeriod",
    "VolAtrTimeframe",
    "DevFactor",
    "VolMaxTrades",
    "ST1_Timeframe",
    "Entry_Timing",
    "ST1_HL_strength_L",
    "ST1_HL_strength_R",
    "ST1_countback",
    "ST1_MinDist_to_HL",
    "ST1_MinDist_to_HL_percentage",
    "ST1_UpDiff",
    "ST1_DownDiff",
    "ST1_MaxPendingOrders",
    "MaxTrades",
    "MinDist_orders",
    "ST1_Expiration_hours",
    "EA_MagicNumber",
    "EA_Comment",
    "Exit_Timing",
    "UseEveryTick",
    "Exit_stop",
    "Exit_limit",
    "Exit_TrailSL_size",
    "Exit_TrailSL_Start",
    "Exit_TrailSL_Stop",
    "Exit_TrailSL_step",
    "Exit_TrailTP_size",
    "Exit_TrailTP_Start",
    "Exit_BE_start",
    "Exit_BE_extra_pips",
    "Exit_HL_UseBE",
    "Exit_HL_trailingSL_timeframe",
    "Exit_HL_countback",
    "Exit_HL_trailingSL_candles_LEFT",
    "Exit_HL_trailingSL_candles_RIGHT",
    "Exit_HL_TrailingSL_MinDist",
    "Exit_HL_Minimum_Dist_For_Change",
    "Exit_HL_trailingSL_extra_distance",
    "Exit_TrailSL_after_X_Minutes",
    "Exit_TrailSL_after_X_Minutes_size",
    "Exit_MagicTrail_Mode",
    "Exit_MagicTrail_start",
    "Exit_MagicTrail_delay",
    "Exit_MagicTrail_size",
    "Exit_MagicTrail_BE_extra_pips",
    "Exit_MagicTrail_Adjust_after_X_Minutes",
    "Exit_MagicTrail_Adjust_after_X_Minutes_start",
    "EnableGrid",
    "GridStart",
    "GridStep",
    "GridTiming",
    "GridTakeProfitPips",
    "GridTakeProfitUSD",
    "GridLossUSD",
    "StopTradingAfterGridLoss",
    "GridMultiplier",
    "CloseAtMinEquity",
    "CloseAtMaxEquity",
    "PropFirmMaxDailyDD",
    "RandomizationValue",
    "ManualBalance",
    "LotsAdjustMinChangePercent",
    "AdjustLotsizeToVariableValues",
    "Risk",
    "StartLots",
    "Manual_RiskPerTrade",
    "MaxRiskInDollar_input",
    "LotPerBalance_step",
    "MaxRiskPerStrategy_Value",
    "HistoricalMaxDD",
    "MaxLots",
    "UseEquity",
    "OnlyUp",
    "ResetHighestBalance",
    "CheckMargin",
    "Broker_GMT_OFFSET_Summer",
    "Broker_GMT_OFFSET_Winter",
    "AutoGMT",
    "UseMQL5Calendar",
    "EnableNFP_Filter",
    "NFP_CloseOpenTrades",
    "NFP_ClosePendingOrders",
    "NFP_MinutesBefore",
    "NFP_MinutesAfter",
    "EnableIR_Filter",
    "IR_CloseOpenTrades",
    "IR_ClosePendingOrders",
    "IR_MinutesBefore",
    "IR_MinutesAfter",
    "EnableCPI_Filter",
    "CPI_CloseOpenTrades",
    "CPI_ClosePendingOrders",
    "CPI_MinutesBefore",
    "CPI_MinutesAfter",
    "confirmationCandleTimeframeA",
    "UseConfirmationCandleA",
    "confirmationCandleTimeframeB",
    "UseConfirmationCandleB",
    "confirmationCandleTimeframeC",
    "UseConfirmationCandleC",
    "confirmationCandleTimeframeD",
    "UseConfirmationCandleD",
    "confirmationCandleTimeframeE",
    "UseConfirmationCandleE",
    "UseTradingTimeZones",
    "KillPending",
    "KillOpen",
    "Time_Source",
    "MondayStart",
    "MondayEnd",
    "TuesdayStart",
    "TuesdayEnd",
    "WednesdayStart",
    "WednesdayEnd",
    "ThursdayStart",
    "ThursdayEnd",
    "FridayStart",
    "FridayEnd",
    "SaturdayStart",
    "SaturdayEnd",
    "SundayStart",
    "SundayEnd",
)


V6_DEFAULT_SET_TEXT = """; saved on 2026.11.04 12:12:02
; this file contains input parameters for testing/optimizing Ultimate_Breakout_EA_WSC_V6.0 expert advisor
; to use it in the strategy tester, click Load in the context menu of the Inputs tab
;
URL=https://forexeasolutions.org
URLB=https://forexeasolutions.services
LICURL=Make sure to add the URLs above to:
LICURLB=MT5 -> Tools -> Options -> Expert Advisors -> Allowed URLs
infopan=------------------------------ InfoPanel Settings  ------------------------------
ShowInfoPanel=true||false||0||true||N
UpdateInfoTesting=false||false||0||true||N
InfoPanelSizeAdjust=1.0||1||0.100000||10.000000||N
SetFontSize=0||0||1||10||N
autoload=------------------------------ AutoLoad Set Files (OneChartSetup)  ------------------------------
UseAutoLoader=false||false||0||true||N
Sets_Folder=Sets
overrideAutoLoadLotsSettings=false||false||0||true||N
overrideNewsFilter=false||false||0||true||N
overrideTimeSettings=false||false||0||true||N
overrideProp=false||false||0||true||N
ForcePendingMax=0||0||1||10||N
CustomOptimization=------------------------------ custom optimization settings ------------------------------
EP=0.0||0.0||0.000000||0.000000||N
RF=0.0||0.0||0.000000||0.000000||N
TR=0.0||0.0||0.000000||0.000000||N
MTR=0.0||0.0||0.000000||0.000000||N
spreadfilter_=------------------------------ spread filter ------------------------------
SpreadFilter=false||false||0||true||N
MaxSpread=3.0||3||1||10||N
DistForSpreadFilter=2||2||2||16||N
BacktestSpeed_string=------------------------------ Backtest Speed settings ------------------------------
BacktestSpeed=1||1||0||4||N
otherfilters_=------------------------------ other settings ------------------------------
PrintLogs=false||false||0||true||N
ForceSymbol=
OnlyLoaderForceSymbol=true||false||0||true||N
setSL_TP_After_Entry=false||false||0||true||N
Virtual_expiration=true||false||0||true||N
useVirtualStops=1||1||0||3||N
VirtualSL_Safety_Hardstop_dist=0.0||0||0.000000||0.000000||N
SetVSL_to_hardSL_sec_delay=0||99||1||990||N
Run_Strategy=1||1||0||2||N
AllowBuyTrades=true||false||0||true||N
AllowSellTrades=true||false||0||true||N
Variable_Values_=------------------------------ Variable Values settings ------------------------------
ATRDefault=0.0||2||0.5||50||N
ATR_Period=30||5||1||150||N
ATR_Timeframe=16408||0||0||49153||N
DefaultValue=0.0||2000.0||200.000000||20000.000000||N
Vol_Entry=------------------------------ Volatility Breakout Entry ------------------------------
VolTimeframe=0||0||0||49153||N
VolCandles=1||1||1||10||N
minSize=0.0||0.0||0.000000||0.000000||N
AtrPeriod=21||21||1||210||N
VolAtrTimeframe=0||0||0||49153||N
DevFactor=2.4||2.4||0.240000||24.000000||N
VolMaxTrades=1||1||1||10||N
ST1_Entry_=------------------------------ Breakout Support/Resistance Entry ------------------------------
ST1_Timeframe=0||0||0||49153||N
Entry_Timing=0||5||0||16385||N
ST1_HL_strength_L=2||2||1||30||Y
ST1_HL_strength_R=2||2||1||20||Y
ST1_countback=250||10||5||250||Y
ST1_MinDist_to_HL=90.0||10||10||500||Y
ST1_MinDist_to_HL_percentage=0.0||0||0.000000||0.000000||N
ST1_UpDiff=20.0||-200||5||200||Y
ST1_DownDiff=40.0||-200||5||200||Y
ST1_MaxPendingOrders=1||5||1||50||N
MaxTrades=1||1||1||30||Y
MinDist_orders=1.0||5||5||100||Y
ST1_Expiration_hours=205||10||5||120||Y
EA_MagicNumber=1000||1000||1||10000||N
EA_Comment=Ultimate Breakout System
Trade_mg_=------------------------------ Trade Exit settings ------------------------------
Exit_Timing=1||5||0||16385||N
UseEveryTick=true||false||0||true||N
Exit_stop=1150.0||500||200||10000||Y
Exit_limit=1300.0||200||50||1500||Y
Trade_Exit_TrailSL_=------------------------------ Trailing SL management ------------------------------
Exit_TrailSL_size=750.0||0.0||5||500||Y
Exit_TrailSL_Start=250.0||0.0||5||500||Y
Exit_TrailSL_Stop=100000.0||200.0||200.0||5000.0||N
Exit_TrailSL_step=0.4||2||0.200000||20.000000||N
Trade_Exit_TrailTP_=------------------------------ Trailing TP management ------------------------------
Exit_TrailTP_size=350.0||0.0||50||2000||Y
Exit_TrailTP_Start=2500.0||0.0||50||2000||Y
BE_Exit_=------------------------------ Break-even SL management ------------------------------
Exit_BE_start=0.0||0||10||300||Y
Exit_BE_extra_pips=0.0||0||10||300||Y
HL_settings_=------------------------------ HIGH/LOW Trailing SL settings ------------------------------
Exit_HL_UseBE=false||false||0||true||N
Exit_HL_trailingSL_timeframe=0||5||0||16385||N
Exit_HL_countback=0||200||1||2000||N
Exit_HL_trailingSL_candles_LEFT=0||10||1||100||N
Exit_HL_trailingSL_candles_RIGHT=0||5||1||50||N
Exit_HL_TrailingSL_MinDist=0||500||500||3000||N
Exit_HL_Minimum_Dist_For_Change=0||10||1||100||N
Exit_HL_trailingSL_extra_distance=0.0||5||0.500000||50.000000||N
TimeTL_=------------------------------ recovery Trailing SL based on time ------------------------------
Exit_TrailSL_after_X_Minutes=0.0||90||9.000000||900.000000||N
Exit_TrailSL_after_X_Minutes_size=0.0||30||3.000000||300.000000||N
MagicTrail_=------------------------------ MagicTrail SL settings ------------------------------
Exit_MagicTrail_Mode=0||0||0||2||N
Exit_MagicTrail_start=0.1||0.1||0.5||10||N
Exit_MagicTrail_delay=1||1||1||5||N
Exit_MagicTrail_size=0.1||0.1||0.010000||1.000000||N
Exit_MagicTrail_BE_extra_pips=1.0||0||0.000000||0.000000||N
Exit_MagicTrail_Adjust_after_X_Minutes=0||0||1||10||N
Exit_MagicTrail_Adjust_after_X_Minutes_start=0.0||0||0.000000||0.000000||N
GridSettings=----------------------- Grid Settings -----------------------
EnableGrid=false||false||0||true||N
GridStart=50.0||50.0||5.000000||500.000000||N
GridStep=20.0||20.0||2.000000||200.000000||N
GridTiming=15||0||0||49153||N
GridTakeProfitPips=100.0||100.0||10.000000||1000.000000||N
GridTakeProfitUSD=50.0||50.0||5.000000||500.000000||N
GridLossUSD=0.0||0.0||0.000000||0.000000||N
StopTradingAfterGridLoss=false||false||0||true||N
GridMultiplier=1.0||1.0||0.100000||10.000000||N
PropFirmSettings=----------------------- Prop Firm Settings -----------------------
CloseAtMinEquity=0.0||0.0||0.000000||0.000000||N
CloseAtMaxEquity=0.0||0.0||0.000000||0.000000||N
PropFirmMaxDailyDD=0.0||0.0||0.000000||0.000000||N
RandomizationValue=0.0||0.0||0.000000||0.000000||N
LotSizeSettings_=----------------------- LotSize Settings -----------------------
ManualBalance=0.0||0.0||0.000000||0.000000||N
LotsAdjustMinChangePercent=5.0||5||0.500000||50.000000||N
AdjustLotsizeToVariableValues=true||false||0||true||N
Risk=0||0||0||20||N
StartLots=0.01||0.01||0.001000||0.100000||N
Manual_RiskPerTrade=0.0||5||1||15||N
MaxRiskInDollar_input=100.0||100.0||10.000000||1000.000000||N
LotPerBalance_step=500.0||100||1||1000||N
MaxRiskPerStrategy_Value=2.0||2.0||0.200000||20.000000||N
HistoricalMaxDD=0.0||0.0||0.000000||0.000000||N
MaxLots=99.0||99||9.900000||990.000000||N
UseEquity=false||false||0||true||N
OnlyUp=true||false||0||true||N
ResetHighestBalance=false||false||0||true||N
CheckMargin=true||false||0||true||N
GMT_Settings_=----------------------- GMT settings -----------------------
Broker_GMT_OFFSET_Summer=3||3||1||30||N
Broker_GMT_OFFSET_Winter=2||2||1||20||N
AutoGMT=true||false||0||true||N
newsFilter=----------------------- News Filter Settings -----------------------
UseMQL5Calendar=true||false||0||true||N
NFP_FILTER_=----------------------- NFP Filter -----------------------
EnableNFP_Filter=false||false||0||true||N
NFP_CloseOpenTrades=true||false||0||true||N
NFP_ClosePendingOrders=true||false||0||true||N
NFP_MinutesBefore=50||50||1||500||N
NFP_MinutesAfter=30||30||1||300||N
IR_FILTER_=----------------------- Interest Rate Filter -----------------------
EnableIR_Filter=false||false||0||true||N
IR_CloseOpenTrades=true||false||0||true||N
IR_ClosePendingOrders=true||false||0||true||N
IR_MinutesBefore=60||60||1||600||N
IR_MinutesAfter=120||120||1||1200||N
CPI_FILTER_=----------------------- CPI Filter -----------------------
EnableCPI_Filter=false||false||0||true||N
CPI_CloseOpenTrades=true||false||0||true||N
CPI_ClosePendingOrders=true||false||0||true||N
CPI_MinutesBefore=60||60||1||600||N
CPI_MinutesAfter=120||120||1||1200||N
FAKE_BREAKOUT_FILTER=----------------------- Fake Breakout Filter -----------------------
confirmationCandleTimeframeA=1||0||0||49153||N
UseConfirmationCandleA=false||false||0||true||N
confirmationCandleTimeframeB=5||0||0||49153||N
UseConfirmationCandleB=false||false||0||true||N
confirmationCandleTimeframeC=15||0||0||49153||N
UseConfirmationCandleC=false||false||0||true||N
confirmationCandleTimeframeD=30||0||0||49153||N
UseConfirmationCandleD=false||false||0||true||N
confirmationCandleTimeframeE=16385||0||0||49153||N
UseConfirmationCandleE=false||false||0||true||N
timefilter_=------------------------- Trading hours -------------------------
UseTradingTimeZones=false||false||0||true||N
KillPending=true||false||0||true||N
KillOpen=false||false||0||true||N
Time_Source=2||0||0||2||N
; Monday Trading Time
MondayStart=00:00
MondayEnd=23:59
; Tuesday Trading Time
TuesdayStart=00:00
TuesdayEnd=23:59
; Wednesday Trading Time
WednesdayStart=00:00
WednesdayEnd=23:59
; Thursday Trading Time
ThursdayStart=00:00
ThursdayEnd=23:59
; Friday Trading Time
FridayStart=00:00
FridayEnd=23:59
; Saturday Trading Time
SaturdayStart=00:00
SaturdayEnd=23:59
; Sunday Trading Time
SundayStart=00:00
SundayEnd=23:59
"""
