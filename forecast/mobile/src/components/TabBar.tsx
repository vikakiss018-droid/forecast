import { Pressable, StyleSheet, Text, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { colors } from "../lib/theme";

export type AppTab = "scan" | "swing" | "stocks" | "paper";

const TABS: { id: AppTab; label: string }[] = [
  { id: "scan", label: "Сканер" },
  { id: "swing", label: "Среднесрок" },
  { id: "stocks", label: "Акции" },
  { id: "paper", label: "Симуляция" },
];

type Props = {
  active: AppTab;
  onChange: (tab: AppTab) => void;
};

export function TabBar({ active, onChange }: Props) {
  const insets = useSafeAreaInsets();
  return (
    <View style={[styles.bar, { paddingBottom: Math.max(insets.bottom, 8) }]}>
      {TABS.map((tab) => {
        const on = tab.id === active;
        return (
          <Pressable
            key={tab.id}
            style={[styles.btn, on && styles.btnActive]}
            onPress={() => onChange(tab.id)}
          >
            <Text style={[styles.label, on && styles.labelActive]}>{tab.label}</Text>
          </Pressable>
        );
      })}
    </View>
  );
}

const styles = StyleSheet.create({
  bar: {
    flexDirection: "row",
    gap: 4,
    paddingHorizontal: 8,
    paddingTop: 8,
    backgroundColor: "rgba(20, 10, 24, 0.96)",
    borderTopWidth: 1,
    borderTopColor: colors.border,
  },
  btn: {
    flex: 1,
    borderRadius: 12,
    paddingVertical: 10,
    alignItems: "center",
  },
  btnActive: {
    backgroundColor: colors.accent,
  },
  label: {
    color: colors.muted,
    fontSize: 11,
    fontWeight: "800",
  },
  labelActive: {
    color: "#2a1020",
  },
});
