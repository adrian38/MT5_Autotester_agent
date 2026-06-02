# UBS EA Parameters Reference

Source: Ultimate Breakout System Manual V5.0

## .set file format

Each parameter line follows this structure:

```
KEY=value||default||step||max||Y/N
```

| Field | Meaning |
|-------|---------|
| `value` | Current value used in this seed |
| `default` | Default/optimization start value |
| `step` | Optimization step |
| `max` | Optimization maximum |
| `Y` | MT5 will **optimize** this parameter |
| `N` | Parameter is **fixed** during optimization |

Section header lines look like:
```
SectionKey=----- Section label -----
```

---

## Section: Display (top of file)

| Key | Description | Opt |
|-----|-------------|-----|
| `ShowInfoPanel` | Show information panel on chart | N |
| `UpdateInfoTesting` | Update panel during backtesting | N |
| `InfoPanelSizeAdjust` | Adjust info panel size multiplier | N |

---

## Section: CustomOptimization — Custom optimization scores

| Key | Description | Opt |
|-----|-------------|-----|
| `EP` | Expected Profit target (custom optimization criterion) | N |
| `RF` | Recovery Factor target (custom optimization criterion) | N |
| `TR` | Target Return (custom optimization criterion) | N |

---

## Section: spreadfilter — Spread / slippage filter

| Key | Description | Opt |
|-----|-------------|-----|
| `SpreadFilter` | Enable spread filter (blocks trades when spread is too wide) | N |
| `MaxSpread` | Maximum allowed spread in pips | N |
| `DistForSpreadFilter` | Distance in pips for spread filter activation | N |

---

## Section: otherfilters — Other filters

| Key | Description | Opt |
|-----|-------------|-----|
| `setSL_TP_After_Entry` | Set SL/TP after fill, not in the pending order itself | N |
| `Virtual_expiration` | Use virtual order expiration instead of broker expiration | N |
| `useVirtualStops` | Virtual stops mode: 0=off, 1=virtual SL, 2=virtual TP, 3=both | N |
| `VirtualSL_Safety_Hardstop_dist` | Hard stop distance for virtual SL (0=disabled) | N |
| `SetVSL_to_hardSL_sec_delay` | Seconds delay before converting virtual SL to hard SL | N |

---

## Section: Variable_Values — Variable values settings

| Key | Description | Opt |
|-----|-------------|-----|
| `ATRDefault` | ATR default value override (0 = use calculated ATR) | N |
| `ATR_Period` | ATR period in bars | N |
| `ATR_Timeframe` | Timeframe used for ATR calculation (MT5 timeframe enum) | N |
| `DefaultValue` | Default variable value placeholder | N |

---

## Section: ST1_Entry — Strategy 1 Trade Entry

| Key | Description | Opt |
|-----|-------------|-----|
| `AllowBuyTrades` | Allow long (buy) trades | N |
| `AllowSellTrades` | Allow short (sell) trades | N |
| `ST1_Timeframe` | Timeframe for strategy 1 (0 = chart timeframe) | N |
| `Entry_Timing` | Entry timing mode (MT5 timeframe enum) | N |
| `ST1_HL_strength_L` | Bars to the LEFT of pivot to validate High/Low | **Y** |
| `ST1_HL_strength_R` | Bars to the RIGHT of pivot to validate High/Low | **Y** |
| `ST1_countback` | Number of bars to look back when searching for High/Low | **Y** |
| `ST1_MinDist_to_HL` | Minimum distance from current price to H/L in pips | **Y** |
| `ST1_MinDist_to_HL_percentage` | Minimum distance to H/L as percentage (0=disabled) | N |
| `ST1_UpDiff` | Entry offset above the High in pips (negative = inside range) | **Y** |
| `ST1_DownDiff` | Entry offset below the Low in pips (negative = inside range) | **Y** |
| `ST1_MaxPendingOrders` | Maximum simultaneous pending orders | N |
| `MaxTrades` | Maximum simultaneous open trades | N |
| `MinDist_orders` | Minimum distance between pending orders in pips | **Y** |
| `ST1_Expiration_hours` | Pending order expiration time in hours | **Y** |
| `EA_MagicNumber` | EA magic number — unique identifier for this EA's trades | N |
| `EA_Comment` | Comment string attached to trades | N |

---

## Section: Trade_mg — Trade Exit management

| Key | Description | Opt |
|-----|-------------|-----|
| `Exit_Timing` | Exit timing mode (MT5 timeframe enum) | N |
| `UseEveryTick` | Use every tick for exit calculations (false = bar close only) | N |
| `Exit_stop` | Stop Loss size in pips | **Y** |
| `Exit_limit` | Take Profit size in pips | **Y** |

---

## Section: Trade_Exit_TrailSL — Trailing Stop Loss

| Key | Description | Opt |
|-----|-------------|-----|
| `Exit_TrailSL_size` | Trailing SL distance in pips | **Y** |
| `Exit_TrailSL_Start` | Profit in pips required to activate trailing SL (0=immediate) | **Y** |
| `Exit_TrailSL_Stop` | Stop level for trailing SL in pips | **Y** |
| `Exit_TrailSL_step` | Trailing SL adjustment step | N |

---

## Section: Trade_Exit_TrailTP — Trailing Take Profit

| Key | Description | Opt |
|-----|-------------|-----|
| `Exit_TrailTP_size` | Trailing TP distance in pips (0=disabled) | Y/N |
| `Exit_TrailTP_Start` | Profit in pips required to activate trailing TP | Y/N |

*Note: optimizable flag varies per seed.*

---

## Section: BE_Exit — Break-even SL

| Key | Description | Opt |
|-----|-------------|-----|
| `Exit_BE_start` | Profit in pips to move SL to break-even (0=disabled) | Y/N |
| `Exit_BE_extra_pips` | Extra pips above entry price for break-even SL | Y/N |

*Note: optimizable flag varies per seed.*

---

## Section: HL_settings — High/Low Trailing SL

| Key | Description | Opt |
|-----|-------------|-----|
| `Exit_HL_UseBE` | Use break-even logic for H/L trailing SL | N |
| `Exit_HL_trailingSL_timeframe` | Timeframe for H/L trailing SL pivots | N |
| `Exit_HL_countback` | Bars to look back for H/L trailing pivots | N |
| `Exit_HL_trailingSL_candles_LEFT` | Left bars for H/L trailing pivot | N |
| `Exit_HL_trailingSL_candles_RIGHT` | Right bars for H/L trailing pivot | N |
| `Exit_HL_TrailingSL_MinDist` | Minimum distance to trigger H/L SL change | N |
| `Exit_HL_Minimum_Dist_For_Change` | Minimum profit before H/L SL moves | N |
| `Exit_HL_trailingSL_extra_distance` | Extra distance buffer for H/L trailing SL | N |

---

## Section: TimeTL — Time-based Trailing SL

| Key | Description | Opt |
|-----|-------------|-----|
| `Exit_TrailSL_after_X_Minutes` | Minutes in trade before time-based trailing SL activates (0=off) | N |
| `Exit_TrailSL_after_X_Minutes_size` | Trailing SL size when time-based mode activates | N |

---

## Section: MagicTrail — MagicTrail SL

| Key | Description | Opt |
|-----|-------------|-----|
| `Exit_MagicTrail_Mode` | MagicTrail mode: 0=off, 1=SL only, 2=SL+TP | N |
| `Exit_MagicTrail_start` | Activation level for MagicTrail | N |
| `Exit_MagicTrail_delay` | Delay in bars before MagicTrail adjusts | N |
| `Exit_MagicTrail_size` | MagicTrail trailing size | N |
| `Exit_MagicTrail_BE_extra_pips` | Break-even extra pips for MagicTrail | N |
| `Exit_MagicTrail_Adjust_after_X_Minutes` | Adjust MagicTrail after X minutes (0=off) | N |
| `Exit_MagicTrail_Adjust_after_X_Minutes_start` | Start level for time-based MagicTrail adjustment | N |

---

## Section: LotSizeSettings — Lot size management

| Key | Description | Opt |
|-----|-------------|-----|
| `LotsAdjustMinChangePercent` | Minimum % change required to adjust lot size | N |
| `Risk` | Risk mode: 0=fixed lots, 1=% risk per trade, 2=lot per balance step, 3=manual | N |
| `StartLots` | Fixed lot size when Risk=0 | N |
| `Manual_RiskPerTrade` | Risk % per trade when Risk=1 | N |
| `LotPerBalance_step` | Balance increment per 0.01 lot when Risk=2 | N |
| `MaxLots` | Maximum allowed lot size | N |
| `UseEquity` | Use equity (not balance) for lot calculation | N |
| `OnlyUp` | Only increase lot size, never reduce during a session | N |
| `CheckMargin` | Check available margin before placing order | N |

---

## Section: GMT_Settings — GMT offset

| Key | Description | Opt |
|-----|-------------|-----|
| `Broker_GMT_OFFSET_Summer` | Broker GMT offset during summer (DST) | N |
| `Broker_GMT_OFFSET_Winter` | Broker GMT offset during winter | N |
| `AutoGMT` | Automatically detect broker GMT offset | N |

---

## Section: NFP_FILTER — NFP news filter

| Key | Description | Opt |
|-----|-------------|-----|
| `EnableNFP_Filter` | Enable NFP (Non-Farm Payroll) trading pause | N |
| `NFP_CloseOpenTrades` | Close open trades before NFP announcement | N |
| `NFP_ClosePendingOrders` | Cancel pending orders before NFP | N |
| `NFP_MinutesBefore` | Minutes before NFP to pause trading | N |
| `NFP_MinutesAfter` | Minutes after NFP before resuming trading | N |

---

## Section: IR_FILTER — Interest Rate filter

| Key | Description | Opt |
|-----|-------------|-----|
| `EnableIR_Filter` | Enable Interest Rate decision trading pause | N |
| `IR_CloseOpenTrades` | Close open trades before IR announcement | N |
| `IR_ClosePendingOrders` | Cancel pending orders before IR announcement | N |
| `IR_MinutesBefore` | Minutes before IR to pause trading | N |
| `IR_MinutesAfter` | Minutes after IR before resuming trading | N |

---

## Section: CPI_FILTER — CPI filter

| Key | Description | Opt |
|-----|-------------|-----|
| `EnableCPI_Filter` | Enable CPI (Consumer Price Index) trading pause | N |
| `CPI_CloseOpenTrades` | Close open trades before CPI release | N |
| `CPI_ClosePendingOrders` | Cancel pending orders before CPI | N |
| `CPI_MinutesBefore` | Minutes before CPI to pause trading | N |
| `CPI_MinutesAfter` | Minutes after CPI before resuming trading | N |

---

## Section: timefilter — Trading hours

| Key | Description | Opt |
|-----|-------------|-----|
| `UseTradingTimeZones` | Enable trading hours filter | N |
| `KillPending` | Cancel pending orders outside allowed trading hours | N |
| `KillOpen` | Close open trades outside allowed trading hours | N |
| `Time_Source` | Time source: 0=broker time, 1=local time, 2=server time | N |
| `MondayStart` / `MondayEnd` | Monday trading window (HH:MM format) | N |
| `TuesdayStart` / `TuesdayEnd` | Tuesday trading window | N |
| `WednesdayStart` / `WednesdayEnd` | Wednesday trading window | N |
| `ThursdayStart` / `ThursdayEnd` | Thursday trading window | N |
| `FridayStart` / `FridayEnd` | Friday trading window | N |
| `SaturdayStart` / `SaturdayEnd` | Saturday trading window | N |
| `SundayStart` / `SundayEnd` | Sunday trading window | N |

---

## Key optimizable parameters summary (Y flag)

These are the parameters the UBS agent mutates during generation:

| Key | Range (typical) | Description |
|-----|----------------|-------------|
| `ST1_HL_strength_L` | 1–30 | Left pivot bars |
| `ST1_HL_strength_R` | 1–20 | Right pivot bars |
| `ST1_countback` | 10–250 | Lookback bars for H/L |
| `ST1_MinDist_to_HL` | 5–100 pips | Min distance to H/L |
| `ST1_UpDiff` | −20 to +20 pips | Entry offset above High |
| `ST1_DownDiff` | −20 to +20 pips | Entry offset below Low |
| `MinDist_orders` | 5–50 pips | Min distance between orders |
| `ST1_Expiration_hours` | 24–500 h | Order expiry |
| `Exit_stop` | 10–500 pips | Stop Loss |
| `Exit_limit` | 10–500 pips | Take Profit |
| `Exit_TrailSL_size` | 5–200 pips | Trailing SL distance |
| `Exit_TrailSL_Start` | 5–200 pips | Trailing SL activation |
| `Exit_TrailSL_Stop` | 10–300 pips | Trailing SL stop |
| `Exit_TrailTP_size` | varies | Trailing TP distance |
| `Exit_TrailTP_Start` | varies | Trailing TP activation |
| `Exit_BE_start` | varies | Break-even activation |
| `Exit_BE_extra_pips` | varies | Break-even extra pips |
