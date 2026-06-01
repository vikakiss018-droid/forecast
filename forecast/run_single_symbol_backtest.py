"""CLI: single-symbol 1h backtest (indicators only, no kNN)."""

from __future__ import annotations

import os

from .paths import load_project_env
from .single_symbol_backtest import SingleSymbolBacktestConfig, run_single_symbol_backtest


def main() -> int:
    load_project_env(force=True)
    symbol = os.environ.get("SINGLE_BT_SYMBOL", "SOL/USDT").strip() or "SOL/USDT"
    tf = os.environ.get("SINGLE_BT_TIMEFRAME", "1h").strip() or "1h"
    deposit = float(os.environ.get("SINGLE_BT_DEPOSIT", "1000"))
    risk = os.environ.get("SINGLE_BT_RISK_PCT", "0.5").strip()
    risk_f = float(risk) if risk else 0.5

    print(f"[single_bt] {symbol} {tf} indicators-only (no kNN)...", flush=True)
    result = run_single_symbol_backtest(
        cfg=SingleSymbolBacktestConfig(symbol=symbol, timeframe=tf),
        deposit_usdt=deposit,
        risk_pct=risk_f,
    )
    if result.get("status") != "done":
        print(f"[single_bt] failed: {result.get('error')}", flush=True)
        return 1

    s = result["summary"]
    print(
        f"[single_bt] trades={s['trades']} win%={s['win_rate_pct']} "
        f"total_R={s['total_r']} PF={s.get('profit_factor')}",
        flush=True,
    )
    print(
        f"[single_bt] deposit=${s['deposit_usdt']} risk={s['risk_pct']}% "
        f"→ est. PnL ${s['estimated_profit_usdt']} balance ${s['estimated_balance_usdt']} "
        f"({s['estimated_return_pct']}%)",
        flush=True,
    )
    print(f"[single_bt] saved {result.get('symbol')} {result.get('period_from')} → {result.get('period_to')}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
