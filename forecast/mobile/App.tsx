import { useCallback, useEffect, useState } from "react";
import { StatusBar } from "expo-status-bar";
import { SafeAreaProvider, SafeAreaView } from "react-native-safe-area-context";

import type { ServerSettings } from "./src/api/types";
import { HomeScreen } from "./src/screens/HomeScreen";
import { SettingsScreen } from "./src/screens/SettingsScreen";
import { loadSettings, saveSettings } from "./src/lib/settings";
import { colors } from "./src/lib/theme";

type Screen = "home" | "settings";

export default function App() {
  const [screen, setScreen] = useState<Screen>("home");
  const [settings, setSettings] = useState<ServerSettings | null>(null);

  useEffect(() => {
    loadSettings().then(setSettings);
  }, []);

  const handleSave = useCallback(async (next: ServerSettings) => {
    await saveSettings(next);
    setSettings(next);
  }, []);

  if (!settings) {
    return null;
  }

  return (
    <SafeAreaProvider>
      <SafeAreaView style={{ flex: 1, backgroundColor: colors.bg }} edges={["top", "left", "right"]}>
        <StatusBar style="light" />
        {screen === "home" ? (
          <HomeScreen settings={settings} onOpenSettings={() => setScreen("settings")} />
        ) : (
          <SettingsScreen
            settings={settings}
            onSave={handleSave}
            onBack={() => setScreen("home")}
          />
        )}
      </SafeAreaView>
    </SafeAreaProvider>
  );
}
