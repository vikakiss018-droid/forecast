import type { ReactNode } from "react";
import { useState } from "react";
import {
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Switch,
  Text,
  TextInput,
  View,
} from "react-native";

import { registerExpoToken, testConnection, unregisterExpoToken } from "../api/client";
import type { ServerSettings } from "../api/types";
import {
  ensureNotificationPermissions,
  getExpoPushToken,
} from "../lib/notifications";
import { colors } from "../lib/theme";

type Props = {
  settings: ServerSettings;
  onSave: (next: ServerSettings) => Promise<void>;
  onBack: () => void;
};

export function SettingsScreen({ settings, onSave, onBack }: Props) {
  const [draft, setDraft] = useState(settings);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const update = (patch: Partial<ServerSettings>) => {
    setDraft((prev) => ({ ...prev, ...patch }));
  };

  const save = async () => {
    setBusy(true);
    setMessage(null);
    try {
      await testConnection(draft.baseUrl, draft.username, draft.password);
      await onSave(draft);

      if (draft.notifyEnabled) {
        const ok = await ensureNotificationPermissions();
        if (!ok) {
          setMessage("Разрешите уведомления в настройках телефона");
          return;
        }
        const token = await getExpoPushToken();
        if (token) {
          await registerExpoToken(draft.baseUrl, draft.username, draft.password, token, Platform.OS);
          setMessage("Сохранено. Пуш при score > 35 включён.");
        } else {
          setMessage("Сохранено. Для push нужен физический телефон и сборка через EAS.");
        }
      } else {
        const token = await getExpoPushToken();
        if (token) {
          await unregisterExpoToken(draft.baseUrl, draft.username, draft.password, token);
        }
        setMessage("Сохранено. Уведомления выключены.");
      }
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "Не удалось сохранить");
    } finally {
      setBusy(false);
    }
  };

  return (
    <ScrollView style={styles.root} contentContainerStyle={styles.content}>
      <Pressable onPress={onBack}>
        <Text style={styles.back}>← Назад</Text>
      </Pressable>
      <Text style={styles.title}>Настройки</Text>
      <Text style={styles.hint}>
        Укажите адрес вашего Forecast-сервера. Логин и пароль — те же, что для панели `/scanner`.
      </Text>

      <Field label="Адрес сервера">
        <TextInput
          style={styles.input}
          autoCapitalize="none"
          autoCorrect={false}
          keyboardType="url"
          placeholder="http://123.45.67.89:8000"
          placeholderTextColor={colors.muted}
          value={draft.baseUrl}
          onChangeText={(baseUrl) => update({ baseUrl })}
        />
      </Field>

      <Field label="Логин">
        <TextInput
          style={styles.input}
          autoCapitalize="none"
          autoCorrect={false}
          value={draft.username}
          onChangeText={(username) => update({ username })}
        />
      </Field>

      <Field label="Пароль">
        <TextInput
          style={styles.input}
          secureTextEntry
          autoCapitalize="none"
          autoCorrect={false}
          value={draft.password}
          onChangeText={(password) => update({ password })}
        />
      </Field>

      <View style={styles.switchRow}>
        <View style={{ flex: 1 }}>
          <Text style={styles.switchTitle}>Уведомления score &gt; 35</Text>
          <Text style={styles.switchHint}>Сервер шлёт push после каждого нового скана</Text>
        </View>
        <Switch
          value={draft.notifyEnabled}
          onValueChange={(notifyEnabled) => update({ notifyEnabled })}
          trackColor={{ false: "#3a1e48", true: colors.accent }}
          thumbColor="#fff0f8"
        />
      </View>

      <Pressable style={[styles.saveBtn, busy && styles.saveBtnDisabled]} onPress={save} disabled={busy}>
        <Text style={styles.saveText}>{busy ? "Сохранение…" : "Сохранить и проверить"}</Text>
      </Pressable>

      {message ? <Text style={styles.message}>{message}</Text> : null}

      <Text style={styles.footer}>
        Для установки как отдельное приложение соберите APK/IPA через EAS Build (см. README в папке mobile).
      </Text>
    </ScrollView>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <View style={styles.field}>
      <Text style={styles.label}>{label}</Text>
      {children}
    </View>
  );
}

const styles = StyleSheet.create({
  root: {
    flex: 1,
    backgroundColor: colors.bg,
  },
  content: {
    padding: 16,
    paddingBottom: 40,
  },
  back: {
    color: colors.accent,
    fontSize: 16,
    marginBottom: 12,
  },
  title: {
    color: colors.text,
    fontSize: 24,
    fontWeight: "800",
    marginBottom: 8,
  },
  hint: {
    color: colors.muted,
    fontSize: 14,
    lineHeight: 20,
    marginBottom: 18,
  },
  field: {
    marginBottom: 14,
  },
  label: {
    color: colors.muted,
    fontSize: 12,
    marginBottom: 6,
    textTransform: "uppercase",
  },
  input: {
    borderWidth: 1,
    borderColor: colors.border,
    backgroundColor: "rgba(20, 10, 28, 0.55)",
    color: colors.text,
    borderRadius: 14,
    paddingHorizontal: 12,
    paddingVertical: 12,
    fontSize: 15,
  },
  switchRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    marginVertical: 18,
    padding: 14,
    borderRadius: 16,
    borderWidth: 1,
    borderColor: colors.border,
    backgroundColor: "rgba(255, 240, 248, 0.04)",
  },
  switchTitle: {
    color: colors.text,
    fontWeight: "700",
    fontSize: 15,
  },
  switchHint: {
    color: colors.muted,
    fontSize: 12,
    marginTop: 4,
  },
  saveBtn: {
    backgroundColor: colors.accent,
    borderRadius: 999,
    paddingVertical: 14,
    alignItems: "center",
  },
  saveBtnDisabled: {
    opacity: 0.6,
  },
  saveText: {
    color: "#2a1020",
    fontWeight: "800",
    fontSize: 15,
  },
  message: {
    marginTop: 14,
    color: colors.warn,
    textAlign: "center",
    lineHeight: 20,
  },
  footer: {
    marginTop: 24,
    color: colors.muted,
    fontSize: 12,
    lineHeight: 18,
    textAlign: "center",
  },
});
