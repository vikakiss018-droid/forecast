import { StyleSheet, Text, View } from "react-native";

import type { Setup } from "../api/types";
import { colors, fmtNum } from "../lib/theme";

type Props = {
  setup: Setup;
};

export function SetupCard({ setup }: Props) {
  const directionStyle =
    setup.direction === "Long" ? styles.long : setup.direction === "Short" ? styles.short : styles.muted;
  return (
    <View style={[styles.card, setup.hot && styles.hot]}>
      <View style={styles.top}>
        <Text style={styles.symbol}>{setup.symbol || "—"}</Text>
        <Text style={[styles.direction, directionStyle]}>{setup.direction || "—"}</Text>
        {setup.hot ? <Text style={styles.hotBadge}>ВЫГОДНО</Text> : null}
        <Text style={styles.score}>{fmtNum(setup.score, 1)}</Text>
      </View>
      <View style={styles.grid}>
        <Field label="Паттерн" value={setup.pattern || "—"} />
        <Field label="R:R" value={fmtNum(setup.risk_reward, 2)} />
        <Field label="Вход" value={fmtNum(setup.entry, 4)} mono />
        <Field label="Стоп" value={fmtNum(setup.stop, 4)} mono danger />
        <Field label="TP1" value={fmtNum(setup.target_1, 4)} mono good />
        <Field label="TP2" value={fmtNum(setup.target_2, 4)} mono good />
      </View>
      {setup.why_selected ? <Text style={styles.why}>{setup.why_selected}</Text> : null}
    </View>
  );
}

function Field({
  label,
  value,
  mono,
  danger,
  good,
}: {
  label: string;
  value: string;
  mono?: boolean;
  danger?: boolean;
  good?: boolean;
}) {
  return (
    <View style={styles.field}>
      <Text style={styles.label}>{label}</Text>
      <Text
        style={[
          styles.value,
          mono && styles.mono,
          danger && styles.danger,
          good && styles.good,
        ]}
      >
        {value}
      </Text>
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
    backgroundColor: "rgba(58, 30, 72, 0.95)",
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
  direction: {
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
    backgroundColor: "rgba(255, 240, 248, 0.06)",
  },
  hotBadge: {
    color: "#ffd6ec",
    fontSize: 11,
    fontWeight: "800",
    backgroundColor: "rgba(255, 158, 207, 0.22)",
    paddingHorizontal: 8,
    paddingVertical: 3,
    borderRadius: 999,
    overflow: "hidden",
  },
  score: {
    marginLeft: "auto",
    color: colors.accent2,
    fontSize: 22,
    fontWeight: "800",
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
    letterSpacing: 0.6,
    marginBottom: 4,
  },
  value: {
    color: colors.text,
    fontSize: 14,
  },
  mono: {
    fontFamily: "Menlo",
  },
  danger: {
    color: colors.short,
  },
  good: {
    color: colors.long,
  },
  why: {
    marginTop: 12,
    color: colors.muted,
    fontSize: 13,
    lineHeight: 18,
  },
});
