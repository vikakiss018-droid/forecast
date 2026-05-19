from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

# Ascending expected-EV buckets (for monotonicity of realized returns vs bucket)
EV_BUCKET_ORDER = ["ev_lt_0", "ev_0_0.10pct", "ev_0.10_0.20pct", "ev_gt_0.20pct"]

EFF_NEIGHBORS_BUCKET_ORDER = ["N_eff_lt_3", "N_eff_3_5", "N_eff_5_10", "N_eff_gt_10", "N_eff_nan"]


def effective_neighbors_bucket_label(n: float) -> str:
    """kNN effective sample size buckets (Kish N_eff) for analytics."""
    try:
        x = float(n)
    except (TypeError, ValueError):
        return "N_eff_nan"
    if not np.isfinite(x):
        return "N_eff_nan"
    if x < 3:
        return "N_eff_lt_3"
    if x < 5:
        return "N_eff_3_5"
    if x < 10:
        return "N_eff_5_10"
    return "N_eff_gt_10"


def ev_bucket_label(ev_frac: float) -> str:
    """Bucket forecast EV (fraction of return) for calibration tables."""
    if not np.isfinite(ev_frac):
        return "ev_nan"
    if ev_frac < 0:
        return "ev_lt_0"
    if ev_frac < 0.001:
        return "ev_0_0.10pct"
    if ev_frac < 0.002:
        return "ev_0.10_0.20pct"
    return "ev_gt_0.20pct"


def _trade_row_metrics(rets: np.ndarray) -> dict[str, Any]:
    """PF, expectancy and max drawdown on a compounding equity path built from net_rets only."""
    out: dict[str, Any] = {
        "n_trades": int(rets.size),
        "profit_factor": 0.0,
        "expectancy": float("nan"),
        "max_drawdown": 0.0,
    }
    if rets.size == 0:
        return out
    r = rets.astype(np.float64, copy=False)
    out["expectancy"] = float(np.mean(r))
    out["profit_factor"] = _profit_factor_from_rets(r)
    eq = np.cumprod(1.0 + r)
    if eq.size > 1:
        running_max = np.maximum.accumulate(eq)
        dd = (eq - running_max) / np.maximum(running_max, 1e-12)
        out["max_drawdown"] = float(np.min(dd))
    return out


def _profit_factor_from_rets(rets: np.ndarray) -> float:
    if rets.size == 0:
        return 0.0
    wins = rets[rets > 0]
    losses = rets[rets < 0]
    sum_win = float(wins.sum()) if wins.size else 0.0
    sum_loss = float(losses.sum()) if losses.size else 0.0
    if sum_loss < 0:
        return float(sum_win / abs(sum_loss))
    if sum_win > 0:
        return float("inf")
    return 0.0


def _monotone_violations(ordered_means: list[float]) -> int:
    finite = [x for x in ordered_means if np.isfinite(x)]
    if len(finite) < 2:
        return 0
    v = 0
    for i in range(len(finite) - 1):
        if finite[i] > finite[i + 1]:
            v += 1
    return v


def _spearman_bucket_vs_metric(
    bucket_order: list[str],
    bucket_to_value: dict[str, float],
) -> float:
    """Spearman between bucket index (EV order) and metric; +1 if perfectly monotone up."""
    xs: list[float] = []
    ys: list[float] = []
    for i, b in enumerate(bucket_order):
        if b not in bucket_to_value:
            continue
        y = bucket_to_value[b]
        if not np.isfinite(y):
            continue
        xs.append(float(i))
        ys.append(float(y))
    if len(xs) < 3:
        return float("nan")
    rx = pd.Series(xs).rank(method="average").to_numpy(dtype=float)
    ry = pd.Series(ys).rank(method="average").to_numpy(dtype=float)
    if np.std(rx) < 1e-12 or np.std(ry) < 1e-12:
        return float("nan")
    return float(np.corrcoef(rx, ry)[0, 1])


def _ev_calibration_report(
    bucket_stats: dict[str, dict[str, Any]],
    metric_key: str,
) -> dict[str, Any]:
    means: dict[str, float] = {}
    counts: dict[str, int] = {}
    for b, d in bucket_stats.items():
        if metric_key in d and np.isfinite(float(d[metric_key])):
            means[str(b)] = float(d[metric_key])
        if "count" in d:
            counts[str(b)] = int(d["count"])
    ordered = [means.get(b, float("nan")) for b in EV_BUCKET_ORDER]
    violations = _monotone_violations(ordered)
    rho = _spearman_bucket_vs_metric(EV_BUCKET_ORDER, means)
    n_fin = len([x for x in ordered if np.isfinite(x)])
    is_monotone = violations == 0 and n_fin >= 2
    return {
        "bucket_order": list(EV_BUCKET_ORDER),
        "mean_by_bucket": means,
        "count_by_bucket": {b: counts.get(b, 0) for b in EV_BUCKET_ORDER},
        "strict_monotone_increasing": is_monotone,
        "pairwise_violations": violations,
        "spearman_bucket_vs_metric": rho,
    }


def _trades_per_calendar_day_stats(
    enriched: pd.DataFrame,
    trades: pd.DataFrame,
    *,
    min_trades_per_day_target: int | None,
) -> dict[str, Any] | None:
    """Counts rows with position != 0 per UTC calendar day (enriched index must be DatetimeIndex)."""
    idx = enriched.index
    if not isinstance(idx, pd.DatetimeIndex) or len(idx) == 0:
        return None
    start = idx.min().normalize()
    end = idx.max().normalize()
    calendar_days_span = int((end - start).days) + 1
    calendar_days_span = max(calendar_days_span, 1)

    if trades.empty or not isinstance(trades.index, pd.DatetimeIndex):
        out: dict[str, Any] = {
            "calendar_days_span": calendar_days_span,
            "mean_trades_per_calendar_day": 0.0,
            "median_trades_per_calendar_day_with_activity": 0.0,
            "min_trades_on_a_calendar_day": 0,
            "max_trades_on_a_calendar_day": 0,
            "fraction_calendar_days_with_ge_target": None,
            "meets_mean_trades_per_day_target": None,
        }
        if min_trades_per_day_target is not None:
            out["min_trades_per_day_target"] = int(min_trades_per_day_target)
            out["meets_mean_trades_per_day_target"] = out["mean_trades_per_calendar_day"] >= float(
                min_trades_per_day_target
            )
        return out

    daily = trades.groupby(trades.index.normalize()).size()
    # Full span: days with zero trades count as 0 toward mean
    full_days = pd.date_range(start, end, freq="D", tz=idx.tz)
    daily_re = daily.reindex(full_days, fill_value=0)
    counts = daily_re.to_numpy(dtype=int)
    mean_all = float(counts.mean()) if counts.size else 0.0
    active = counts[counts > 0]
    median_active = float(np.median(active)) if active.size else 0.0
    frac_ge = None
    meets = None
    if min_trades_per_day_target is not None:
        k = int(min_trades_per_day_target)
        frac_ge = float((counts >= k).mean()) if counts.size else 0.0
        meets = mean_all >= float(k)
    return {
        "calendar_days_span": calendar_days_span,
        "mean_trades_per_calendar_day": mean_all,
        "median_trades_per_calendar_day_with_activity": median_active,
        "min_trades_on_a_calendar_day": int(counts.min()) if counts.size else 0,
        "max_trades_on_a_calendar_day": int(counts.max()) if counts.size else 0,
        "fraction_calendar_days_with_ge_target": frac_ge,
        "meets_mean_trades_per_day_target": meets,
        "min_trades_per_day_target": int(min_trades_per_day_target)
        if min_trades_per_day_target is not None
        else None,
    }


def post_trade_analytics(
    enriched: pd.DataFrame,
    *,
    min_regime_trades_for_block_suggest: int = 20,
    min_trades_per_day_target: int | None = None,
) -> dict[str, Any]:
    """
    Summaries from run_enriched_gate_backtest output.
    Expects columns: net_ret, fwd_ret, position, reason_code, ev, ev_bucket, knn_regime (optional),
    eff_neighbors_bucket (optional, for N_eff vs PnL).
    Regime block suggestions: trades >= min_regime_trades_for_block_suggest, PF < 1, expectancy < 0.
    """
    if enriched.empty:
        return {"error": "empty_enriched"}

    eq = enriched["equity"].to_numpy(dtype=float) if "equity" in enriched.columns else np.array([])
    dd = 0.0
    if eq.size > 1:
        running_max = np.maximum.accumulate(eq)
        drawdown = (eq - running_max) / np.maximum(running_max, 1e-12)
        dd = float(np.min(drawdown))

    trades = enriched[enriched.get("position", 0) != 0] if "position" in enriched.columns else enriched.iloc[0:0]
    rets = trades["net_ret"].to_numpy(dtype=float) if "net_ret" in trades.columns and not trades.empty else np.array([])

    win_rate = float((rets > 0).mean()) if rets.size else 0.0
    wins = rets[rets > 0]
    losses = rets[rets < 0]
    avg_win = float(wins.mean()) if wins.size else 0.0
    avg_loss = float(losses.mean()) if losses.size else 0.0
    sum_win = float(wins.sum()) if wins.size else 0.0
    sum_loss = float(losses.sum()) if losses.size else 0.0
    if sum_loss < 0:
        profit_factor = float(sum_win / abs(sum_loss))
    elif sum_win > 0:
        profit_factor = float("inf")
    else:
        profit_factor = 0.0
    expectancy = float(rets.mean()) if rets.size else 0.0

    if "position" in trades.columns and "net_ret" in trades.columns:
        long_rets = trades.loc[trades["position"] == 1, "net_ret"].to_numpy(dtype=float)
        short_rets = trades.loc[trades["position"] == -1, "net_ret"].to_numpy(dtype=float)
        out_by_side = {
            "long": _trade_row_metrics(long_rets),
            "short": _trade_row_metrics(short_rets),
        }
    else:
        out_by_side = {}

    tpd = _trades_per_calendar_day_stats(
        enriched,
        trades,
        min_trades_per_day_target=min_trades_per_day_target,
    )
    out: dict[str, Any] = {
        "n_rows": int(len(enriched)),
        "n_trades": int(len(trades)),
        "win_rate": win_rate,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "profit_factor": profit_factor,
        "max_drawdown": dd,
        "expectancy": expectancy,
        "final_equity": float(enriched["equity"].iloc[-1]) if "equity" in enriched.columns and len(enriched) else 1.0,
    }
    if tpd is not None:
        out["trades_per_calendar_day"] = tpd

    if out_by_side:
        out["by_side"] = out_by_side

    if "reason_code" in enriched.columns:
        vc = enriched["reason_code"].value_counts().to_dict()
        out["gate_reason_distribution"] = {str(k): int(v) for k, v in vc.items()}

    trades_by_regime_detail: dict[str, Any] = {}
    suggested_block: list[str] = []
    if "knn_regime" in enriched.columns and not trades.empty:
        for k, v in trades.groupby("knn_regime", observed=True):
            rr = v["net_ret"].to_numpy(dtype=float)
            n = int(len(rr))
            wr = float((rr > 0).mean()) if n else 0.0
            pf = _profit_factor_from_rets(rr)
            expc = float(rr.mean()) if n else 0.0
            trades_by_regime_detail[str(k)] = {
                "count": n,
                "win_rate": wr,
                "profit_factor": pf,
                "mean_net_ret": expc,
                "expectancy": expc,
            }
            if (
                n >= min_regime_trades_for_block_suggest
                and np.isfinite(pf)
                and pf < 1.0
                and expc < 0.0
            ):
                suggested_block.append(str(k))
        out["trades_by_regime"] = trades_by_regime_detail
        out["suggested_block_knn_regimes"] = suggested_block
        out["suggested_block_knn_regimes_pf_lt_1"] = suggested_block

    if "ev_bucket" in enriched.columns and "fwd_ret" in enriched.columns:
        g2 = enriched.groupby("ev_bucket", observed=True)["fwd_ret"]
        ev_bucket_fwd = {
            str(k): {"count": int(v.count()), "mean_fwd_ret": float(v.mean())} for k, v in g2
        }
        out["ev_bucket_fwd_ret"] = ev_bucket_fwd
        fwd_for_mono = {
            k: {"mean_net_ret": v["mean_fwd_ret"], "count": v["count"]}
            for k, v in ev_bucket_fwd.items()
        }
        out["ev_calibration_fwd_ret"] = _ev_calibration_report(fwd_for_mono, "mean_net_ret")

        if "net_ret" in enriched.columns:
            g3 = enriched[enriched.get("position", 0) != 0].groupby("ev_bucket", observed=True)["net_ret"]
            ev_bucket_actual = {
                str(k): {"count": int(len(v)), "mean_net_ret": float(v.mean())} for k, v in g3
            }
            out["ev_bucket_actual_pnl"] = ev_bucket_actual
            act_for_mono = {
                k: {"mean_net_ret": v["mean_net_ret"], "count": v["count"]}
                for k, v in ev_bucket_actual.items()
            }
            out["ev_calibration_actual_pnl"] = _ev_calibration_report(act_for_mono, "mean_net_ret")

    if "eff_neighbors_bucket" in trades.columns and not trades.empty and "net_ret" in trades.columns:
        g_eff = trades.groupby("eff_neighbors_bucket", observed=True)["net_ret"]
        eff_map = {
            str(bk): {"count": int(v.count()), "mean_net_ret": float(v.mean())} for bk, v in g_eff
        }
        ordered_eff = {b: eff_map[b] for b in EFF_NEIGHBORS_BUCKET_ORDER if b in eff_map}
        out["effective_neighbors_bucket_pnl"] = ordered_eff if ordered_eff else eff_map

    return out
