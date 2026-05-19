from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.neighbors import NearestNeighbors

from .features import make_windowed_matrix
from .signal_combiner import detect_regime


def weighted_quantile(values: np.ndarray, weights: np.ndarray, q: float) -> float:
    """Linear interpolation on cumulative weight (weights need not be normalized)."""
    x = np.asarray(values, dtype=float)
    w = np.asarray(weights, dtype=float)
    if x.size == 0 or w.size != x.size:
        return 0.0
    wsum = float(np.sum(w))
    if wsum <= 0 or not np.isfinite(wsum):
        return float(np.quantile(x, q))
    w = w / wsum
    order = np.argsort(x)
    x = x[order]
    w = w[order]
    cw = np.cumsum(w)
    target = float(np.clip(q, 0.0, 1.0))
    idx = int(np.searchsorted(cw, target, side="left"))
    if idx <= 0:
        return float(x[0])
    if idx >= len(x):
        return float(x[-1])
    w0 = float(cw[idx - 1])
    w1 = float(cw[idx])
    if abs(w1 - w0) < 1e-15:
        return float(x[idx])
    t = (target - w0) / (w1 - w0)
    return float(x[idx - 1] + t * (x[idx] - x[idx - 1]))


@dataclass
class SimilarityConfig:
    window_bars: int = 24
    n_neighbors: int = 20
    forecast_horizon_bars: int = 12
    use_regime_split: bool = True
    use_distance_weights: bool = True
    """Minimum training rows (after excluding current) before falling back to all regimes."""
    regime_min_train_rows: int = 40
    """If effective neighbor count 1/sum(w^2) is below this, use unweighted quantiles for low/high."""
    min_effective_neighbors: float = 5.0


@dataclass
class NeighborForecastStats:
    prob_up: float
    prob_down: float
    prob_flat: float
    avg_up_move: float
    avg_down_move: float
    """avg_up_move / avg_down_move; 0 if not defined."""
    skew: float
    """EV = prob_up * avg_up_move - prob_down * avg_down_move (down move as positive magnitude)."""
    ev: float
    low_ret: float
    high_ret: float
    # Kish N_eff = 1/sum(w^2) for neighbor weights
    effective_neighbors: float = 0.0

    def directional_prob_up(self) -> float:
        """Up vs down mass excluding flat neighbors."""
        s = self.prob_up + self.prob_down
        if s <= 1e-12:
            return 0.5
        return float(self.prob_up / s)


_DIST_EPS = 1e-9


def _regime_tag_per_anchor(df: pd.DataFrame, window_bars: int) -> list[str]:
    """Regime at end of each window row (same order as make_windowed_matrix X)."""
    n = len(df)
    if "knn_regime" in df.columns:
        arr = df["knn_regime"].to_numpy(dtype=object)
        return [str(arr[i]) for i in range(window_bars, n)]
    tags: list[str] = []
    for i in range(window_bars, n):
        tags.append(detect_regime(df.iloc[: i + 1]))
    return tags


def _future_return_at_row(
    close: np.ndarray,
    x_row_ix: int,
    window_bars: int,
    horizon: int,
) -> float | None:
    anchor_bar = x_row_ix + window_bars
    future_bar = anchor_bar + horizon
    if future_bar >= len(close):
        return None
    return float(close[future_bar] / close[anchor_bar] - 1.0)


def _knn_train_indices_and_model(
    X: np.ndarray,
    tags: list[str],
    cfg: SimilarityConfig,
) -> tuple[np.ndarray, NearestNeighbors]:
    """Train kNN on regime-matched rows (excluding last row), with fallback."""
    m = X.shape[0]
    R = tags[-1]
    all_train = np.arange(m - 1, dtype=int)
    tag_arr = np.array(tags, dtype=object)
    regime_train = all_train[tag_arr[all_train] == R]

    min_reg = max(cfg.regime_min_train_rows, cfg.n_neighbors + 1)
    if cfg.use_regime_split and regime_train.size >= min_reg:
        train_idx = regime_train
    else:
        train_idx = all_train

    if train_idx.size < 1:
        train_idx = all_train

    X_train = X[train_idx]
    k = min(cfg.n_neighbors, X_train.shape[0])
    k = max(k, 1)
    nn = NearestNeighbors(n_neighbors=k, metric="euclidean", algorithm="auto")
    nn.fit(X_train)
    return train_idx, nn


def knn_weighted_neighbor_returns(
    df: pd.DataFrame,
    feature_cols: list[str],
    cfg: SimilarityConfig,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Returns (returns, distances, sample_weights) for neighbors of the last window.
    sample_weights sum to 1 when use_distance_weights else uniform.
    """
    X, _idx = make_windowed_matrix(df, feature_cols, cfg.window_bars)
    if X.shape[0] < 2:
        return np.array([]), np.array([]), np.array([])

    close = df["close"].to_numpy(dtype=float)
    tags = _regime_tag_per_anchor(df, cfg.window_bars)
    train_idx, nn = _knn_train_indices_and_model(X, tags, cfg)

    X_train = X[train_idx]
    q = X[-1:].copy()
    dists, knn_cols = nn.kneighbors(q, return_distance=True)
    dists = dists[0].astype(float)
    orig_rows = train_idx[knn_cols[0]]

    rets: list[float] = []
    dlist: list[float] = []
    for row_ix, d in zip(orig_rows.tolist(), dists.tolist()):
        r = _future_return_at_row(close, int(row_ix), cfg.window_bars, cfg.forecast_horizon_bars)
        if r is None:
            continue
        rets.append(r)
        dlist.append(float(d))

    rets_a = np.asarray(rets, dtype=float)
    dists_a = np.asarray(dlist, dtype=float)
    if rets_a.size == 0:
        return rets_a, dists_a, np.array([])

    if cfg.use_distance_weights:
        w = 1.0 / (dists_a + _DIST_EPS)
    else:
        w = np.ones_like(rets_a)
    w = w / np.sum(w)
    return rets_a, dists_a, w


def neighbor_stats_from_returns(
    rets: np.ndarray,
    weights: np.ndarray,
    low_q: float = 0.1,
    high_q: float = 0.9,
    min_effective_neighbors: float = 5.0,
) -> NeighborForecastStats:
    if rets.size == 0 or weights.size == 0:
        return NeighborForecastStats(
            prob_up=0.5,
            prob_down=0.5,
            prob_flat=0.0,
            avg_up_move=0.0,
            avg_down_move=0.0,
            skew=0.0,
            ev=0.0,
            low_ret=0.0,
            high_ret=0.0,
            effective_neighbors=0.0,
        )

    w = weights / np.sum(weights)
    up_m = rets > 0
    dn_m = rets < 0
    fl_m = rets == 0

    prob_up = float(np.sum(w[up_m]))
    prob_down = float(np.sum(w[dn_m]))
    prob_flat = float(np.sum(w[fl_m]))

    w_up = w[up_m]
    r_up = rets[up_m]
    if np.sum(w_up) > 1e-15:
        avg_up_move = float(np.sum(w_up * r_up) / np.sum(w_up))
    else:
        avg_up_move = 0.0

    w_dn = w[dn_m]
    r_dn = rets[dn_m]
    if np.sum(w_dn) > 1e-15:
        avg_down_move = float(np.sum(w_dn * (-r_dn)) / np.sum(w_dn))
    else:
        avg_down_move = 0.0

    if avg_down_move > 1e-15:
        skew = float(avg_up_move / avg_down_move)
    else:
        skew = 0.0

    ev = float(prob_up * avg_up_move - prob_down * avg_down_move)

    w2 = float(np.sum(w * w))
    effective_n = float(1.0 / w2) if w2 > 1e-18 else float(len(w))
    if effective_n < min_effective_neighbors:
        low_ret = float(np.quantile(rets, low_q))
        high_ret = float(np.quantile(rets, high_q))
    else:
        low_ret = float(weighted_quantile(rets, w, low_q))
        high_ret = float(weighted_quantile(rets, w, high_q))

    return NeighborForecastStats(
        prob_up=prob_up,
        prob_down=prob_down,
        prob_flat=prob_flat,
        avg_up_move=avg_up_move,
        avg_down_move=avg_down_move,
        skew=skew,
        ev=ev,
        low_ret=low_ret,
        high_ret=high_ret,
        effective_neighbors=effective_n,
    )


def forecast_neighbor_stats(
    df: pd.DataFrame,
    feature_cols: list[str],
    cfg: SimilarityConfig,
    low_q: float = 0.1,
    high_q: float = 0.9,
) -> NeighborForecastStats:
    """Weighted kNN (optional regime split) + EV / skew / quantile band."""
    rets, _d, w = knn_weighted_neighbor_returns(df, feature_cols, cfg)
    return neighbor_stats_from_returns(
        rets,
        w,
        low_q=low_q,
        high_q=high_q,
        min_effective_neighbors=cfg.min_effective_neighbors,
    )


def forecast_direction(
    df: pd.DataFrame,
    feature_cols: list[str],
    cfg: SimilarityConfig,
) -> tuple[float, float]:
    """Return (prob_up, prob_down) based on similar historical windows."""
    st = forecast_neighbor_stats(df, feature_cols, cfg)
    pu = st.directional_prob_up()
    return pu, 1.0 - pu


def forecast_return_range(
    df: pd.DataFrame,
    feature_cols: list[str],
    cfg: SimilarityConfig,
    low_q: float = 0.1,
    high_q: float = 0.9,
) -> tuple[float, float]:
    """
    Return (low_ret, high_ret) as quantiles of future returns distribution.

    Values are fractional returns, e.g. 0.05 = +5%.
    """
    st = forecast_neighbor_stats(df, feature_cols, cfg, low_q=low_q, high_q=high_q)
    return st.low_ret, st.high_ret
