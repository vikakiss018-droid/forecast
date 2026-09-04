import { useCallback, useEffect, useRef, useState } from "react";
import {
  ActivityIndicator,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";

import { fetchSetups } from "../api/client";
import type { ServerSettings, SetupsResponse } from "../api/types";
import { SetupCard } from "../components/SetupCard";
import { notifyKey, showLocalHotAlert } from "../lib/notifications";
import { colors, fmtTime } from "../lib/theme";

type Props = {
  settings: ServerSettings;
  onOpenSettings: () => void;
};

export function HomeScreen({ settings, onOpenSettings }: Props) {
  const [data, setData] = useState<SetupsResponse | null>(null);
  const [filter, setFilter] = useState<"hot" | "all">("hot");
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const lastKeyRef = useRef("");
  const lastUpdatedRef = useRef<string | null>(null);

  const load = useCallback(
    async (silent = false) => {
      if (!settings.baseUrl || !settings.password) {
        setError("Укажите адрес сервера и пароль в настройках");
        setLoading(false);
        return;
      }
      if (!silent) setLoading(true);
      try {
        const next = await fetchSetups(settings.baseUrl, settings.username, settings.password);
        setData(next);
        setError(null);

        const key = notifyKey(next);
        const isNewScan =
          lastUpdatedRef.current && next.updated_at && next.updated_at !== lastUpdatedRef.current;
        const hot = next.setups.filter((s) => s.hot);
        if (settings.notifyEnabled && hot.length && isNewScan && key !== lastKeyRef.current) {
          const title = hot.length === 1 ? "Выгодная позиция" : `${hot.length} выгодные позиции`;
          const body = hot
            .slice(0, 3)
            .map((s) => `${s.symbol} ${s.direction || ""} · ${s.score}`)
            .join(" · ");
          await showLocalHotAlert(title, body);
        }
        lastKeyRef.current = key;
        lastUpdatedRef.current = next.updated_at || null;
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

  const onRefresh = () => {
    setRefreshing(true);
    load(true);
  };

  const threshold = data?.alert_min_score ?? 35;
  const setups = filter === "hot" ? (data?.setups || []).filter((s) => s.hot) : data?.setups || [];

  return (
    <View style={styles.root}>
      <View style={styles.header}>
        <View>
          <Text style={styles.title}>Выгодные позиции</Text>
          <Text style={styles.subtitle}>
            {data?.timeframe || "—"} · порог {threshold}
          </Text>
        </View>
        <Pressable style={styles.settingsBtn} onPress={onOpenSettings}>
          <Text style={styles.settingsText}>⚙</Text>
        </Pressable>
      </View>

      <View style={styles.stats}>
        <Stat label="Score > порога" value={String(data?.hot_count ?? "—")} />
        <Stat label="Сетапов" value={String(data?.setups?.length ?? "—")} />
        <Stat label="Обновлено" value={fmtTime(data?.updated_at)} small />
      </View>

      <View style={styles.filters}>
        <FilterChip active={filter === "hot"} label="Выгодные" onPress={() => setFilter("hot")} />
        <FilterChip active={filter === "all"} label="Все" onPress={() => setFilter("all")} />
      </View>

      {loading && !data ? (
        <View style={styles.center}>
          <ActivityIndicator color={colors.accent} size="large" />
        </View>
      ) : (
        <ScrollView
          refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={colors.accent} />}
          contentContainerStyle={styles.list}
        >
          {error ? <Text style={styles.error}>{error}</Text> : null}
          {!setups.length ? (
            <Text style={styles.empty}>
              {filter === "hot"
                ? `Пока нет позиций со score выше ${threshold}`
                : "В последнем скане нет сетапов"}
            </Text>
          ) : (
            setups.map((setup) => <SetupCard key={`${setup.symbol}-${setup.score}`} setup={setup} />)
          )}
        </ScrollView>
      )}
    </View>
  );
}

function Stat({ label, value, small }: { label: string; value: string; small?: boolean }) {
  return (
    <View style={styles.stat}>
      <Text style={styles.statLabel}>{label}</Text>
      <Text style={[styles.statValue, small && styles.statSmall]}>{value}</Text>
    </View>
  );
}

function FilterChip({
  active,
  label,
  onPress,
}: {
  active: boolean;
  label: string;
  onPress: () => void;
}) {
  return (
    <Pressable style={[styles.chip, active && styles.chipActive]} onPress={onPress}>
      <Text style={[styles.chipText, active && styles.chipTextActive]}>{label}</Text>
    </Pressable>
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
  statSmall: {
    fontSize: 12,
  },
  filters: {
    flexDirection: "row",
    gap: 8,
    marginBottom: 12,
  },
  chip: {
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: 999,
    paddingHorizontal: 14,
    paddingVertical: 8,
    backgroundColor: "rgba(255, 240, 248, 0.06)",
  },
  chipActive: {
    backgroundColor: colors.accent,
    borderColor: colors.accent,
  },
  chipText: {
    color: colors.text,
    fontWeight: "700",
    fontSize: 13,
  },
  chipTextActive: {
    color: "#2a1020",
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
