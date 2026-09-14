import { useCallback, useEffect, useState } from "react";
import {
  ActivityIndicator,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";

import { fetchPaper } from "../api/client";
import type { PaperResponse, ServerSettings } from "../api/types";
import { TradeCard } from "../components/TradeCard";
import { colors, fmtNum, fmtTime } from "../lib/theme";

type Props = {
  settings: ServerSettings;
  onOpenSettings: () => void;
};

export function PaperScreen({ settings, onOpenSettings }: Props) {
  const [data, setData] = useState<PaperResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(
    async (silent = false) => {
      if (!settings.baseUrl || !settings.password) {
        setError("Укажите адрес сервера и пароль в настройках");
        setLoading(false);
        return;
      }
      if (!silent) setLoading(true);
      try {
        const next = await fetchPaper(settings.baseUrl, settings.username, settings.password);
        setData(next);
        setError(null);
      } catch (e) {
        setError(e instanceof Error ? e.message : "Ошибка загрузки");
      } finally {
        setLoading(false);
        setRefreshing(false);
      }
    },
    [settings],
  );

  useEffect(() => {
    load();
    const timer = setInterval(() => load(true), 20000);
    return () => clearInterval(timer);
  }, [load]);

  const summary = data?.summary;
  const trades = data?.trades || [];
  const totalR = Number(summary?.total_r);
  const totalRText =
    summary == null || Number.isNaN(totalR)
      ? "—"
      : `${totalR > 0 ? "+" : ""}${fmtNum(totalR, 2)}`;

  return (
    <View style={styles.root}>
      <View style={styles.header}>
        <View>
          <Text style={styles.title}>Симуляция</Text>
          <Text style={styles.subtitle}>
            порог {fmtNum(summary?.min_score, 0)} · {fmtTime(summary?.updated_at)}
          </Text>
        </View>
        <Pressable style={styles.settingsBtn} onPress={onOpenSettings}>
          <Text style={styles.settingsText}>⚙</Text>
        </Pressable>
      </View>

      <View style={styles.stats}>
        <Stat label="Открыто" value={String(summary?.open ?? "—")} />
        <Stat label="Win%" value={summary ? `${fmtNum(summary.win_rate_pct, 1)}%` : "—"} />
        <Stat label="Total R" value={totalRText} />
      </View>

      {loading && !data ? (
        <View style={styles.center}>
          <ActivityIndicator color={colors.accent} size="large" />
        </View>
      ) : (
        <ScrollView
          refreshControl={
            <RefreshControl
              refreshing={refreshing}
              onRefresh={() => {
                setRefreshing(true);
                load(true);
              }}
              tintColor={colors.accent}
            />
          }
          contentContainerStyle={styles.list}
        >
          {error ? <Text style={styles.error}>{error}</Text> : null}
          {!trades.length ? (
            <Text style={styles.empty}>
              Симуляции появятся, когда скан на сервере найдёт сетап выше порога
            </Text>
          ) : (
            trades.map((trade, index) => (
              <TradeCard key={String(trade.id || `${trade.symbol}-${index}`)} trade={trade} />
            ))
          )}
        </ScrollView>
      )}
    </View>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <View style={styles.stat}>
      <Text style={styles.statLabel}>{label}</Text>
      <Text style={styles.statValue}>{value}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  root: {
    flex: 1,
    backgroundColor: colors.bg,
    paddingHorizontal: 14,
  },
  header: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "flex-start",
    marginBottom: 14,
    marginTop: 8,
  },
  title: {
    color: colors.text,
    fontSize: 24,
    fontWeight: "800",
  },
  subtitle: {
    color: colors.muted,
    fontSize: 13,
    marginTop: 4,
  },
  settingsBtn: {
    width: 42,
    height: 42,
    borderRadius: 21,
    borderWidth: 1,
    borderColor: colors.border,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: "rgba(255, 240, 248, 0.06)",
  },
  settingsText: {
    fontSize: 20,
  },
  stats: {
    flexDirection: "row",
    gap: 8,
    marginBottom: 12,
  },
  stat: {
    flex: 1,
    backgroundColor: "rgba(255, 240, 248, 0.06)",
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: 16,
    padding: 10,
  },
  statLabel: {
    color: colors.muted,
    fontSize: 10,
    textTransform: "uppercase",
  },
  statValue: {
    color: colors.text,
    fontSize: 16,
    fontWeight: "700",
    marginTop: 4,
  },
  list: {
    paddingBottom: 32,
  },
  center: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
  },
  empty: {
    color: colors.muted,
    textAlign: "center",
    padding: 32,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: 20,
    borderStyle: "dashed",
  },
  error: {
    color: colors.short,
    marginBottom: 12,
    textAlign: "center",
  },
});
