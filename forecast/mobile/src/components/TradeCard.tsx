import { StyleSheet, Text, View } from "react-native";

import type { PaperTrade } from "../api/types";
import { colors, fmtNum } from "../lib/theme";

type Props = {
  trade: PaperTrade;
};

export function TradeCard({ trade }: Props) {
  const open = trade.status === "open";
  const r = trade.r;
  const rText = r == null ? "—" : `${r > 0 ? "+" : ""}${fmtNum(r, 2)}R`;
  const rStyle = r == null || r === 0 ? styles.muted : r > 0 ? styles.good : styles.danger;
  const sideStyle =
    trade.side === "Long" ? styles.long : trade.side === "Short" ? styles.short : styles.muted;
  const status = open ? "OPEN" : trade.win ? "WIN" : "LOSS";

  return (
    <View style={[styles.card, trade.win ? styles.hot : null]}>
      <View style={styles.top}>
        <Text style={styles.symbol}>{trade.symbol || "—"}</Text>
        <Text style={[styles.badge, sideStyle]}>{trade.side || "—"}</Text>
        <Text style={styles.status}>{status}</Text>
        <Text style={[styles.r, rStyle]}>{rText}</Text>
      </View>
      <View style={styles.grid}>
        <Field label="Вход" value={fmtNum(trade.entry, 4)} />
        <Field label="Сейчас" value={fmtNum(trade.last_price, 4)} />
        <Field label="Стоп" value={fmtNum(trade.stop, 4)} danger />
        <Field label="TP" value={fmtNum(trade.tp, 4)} good />
      </View>
      {!open && trade.reason ? <Text style={styles.why}>{trade.reason}</Text> : null}
    </View>
  );
}

function Field({
  label,
  value,
  danger,
  good,
}: {
  label: string;
  value: string;
  danger?: boolean;
  good?: boolean;
}) {
  return (
    <View style={styles.field}>
      <Text style={styles.label}>{label}</Text>
      <Text style={[styles.value, danger && styles.danger, good && styles.good]}>{value}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: colors.card,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: 20,
    padding: 14,
    marginBottom: 12,
  },
  hot: {
    borderColor: "rgba(255, 158, 207, 0.65)",
  },
  top: {
    flexDirection: "row",
    alignItems: "center",
    flexWrap: "wrap",
    gap: 8,
    marginBottom: 12,
  },
  symbol: {
    color: colors.accent,
    fontSize: 18,
    fontWeight: "800",
  },
  badge: {
    fontSize: 12,
    fontWeight: "800",
    paddingHorizontal: 8,
    paddingVertical: 3,
    borderRadius: 999,
    overflow: "hidden",
  },
  long: {
    color: colors.long,
    backgroundColor: "rgba(184, 245, 212, 0.15)",
  },
  short: {
    color: colors.short,
    backgroundColor: "rgba(255, 158, 184, 0.18)",
  },
  muted: {
    color: colors.muted,
  },
  status: {
    color: colors.muted,
    fontSize: 11,
    fontWeight: "800",
  },
  r: {
    marginLeft: "auto",
    fontSize: 20,
    fontWeight: "800",
  },
  good: {
    color: colors.long,
  },
  danger: {
    color: colors.short,
  },
  grid: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 10,
  },
  field: {
    width: "47%",
  },
  label: {
    color: colors.muted,
    fontSize: 10,
    textTransform: "uppercase",
    marginBottom: 4,
  },
  value: {
    color: colors.text,
    fontSize: 14,
    fontFamily: "Menlo",
  },
  why: {
    marginTop: 12,
    color: colors.muted,
    fontSize: 13,
  },
});
