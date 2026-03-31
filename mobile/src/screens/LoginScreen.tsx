import { useMemo, useState } from "react";
import {
  ActivityIndicator,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { useMutation } from "@tanstack/react-query";
import { SafeAreaView } from "react-native-safe-area-context";

import { colors, radii, shadowMd } from "../design/tokens";
import { API_BASE, API_BASE_HELP, loginWithPassword } from "../lib/api";
import { useAuthStore } from "../store/auth";

function validate(email: string, password: string) {
  const trimmedEmail = email.trim();
  if (!trimmedEmail) return "请输入邮箱地址";
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(trimmedEmail)) return "请输入有效的邮箱地址";
  if (!password.trim()) return "请输入密码";
  return null;
}

export function LoginScreen() {
  const setToken = useAuthStore((state) => state.setToken);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [localError, setLocalError] = useState<string | null>(null);

  const configHint = useMemo(() => {
    if (API_BASE) return API_BASE;
    return API_BASE_HELP;
  }, []);

  const loginMutation = useMutation({
    mutationFn: async () => {
      const error = validate(email, password);
      if (error) throw new Error(error);
      return loginWithPassword(email, password);
    },
    onSuccess: async (data) => {
      setLocalError(null);
      await setToken(data.access_token);
    },
    onError: (error: Error) => setLocalError(error.message),
  });

  return (
    <SafeAreaView style={styles.safeArea} edges={["top", "bottom"]}>
      <KeyboardAvoidingView
        style={styles.keyboard}
        behavior={Platform.OS === "ios" ? "padding" : undefined}
      >
        <ScrollView
          contentContainerStyle={styles.scrollContent}
          keyboardShouldPersistTaps="handled"
          showsVerticalScrollIndicator={false}
        >
          <View style={styles.contentWrap}>
            {/* Logo & brand */}
            <View style={styles.hero}>
              <View style={styles.logoBox}>
                <Text style={styles.logoEmoji}>✨</Text>
              </View>
              <Text style={styles.brand}>PAI</Text>
              <Text style={styles.subtitle}>你的个人 AI 助手</Text>
            </View>

            {/* Login card */}
            <View style={styles.card}>
              <View style={styles.field}>
                <Text style={styles.label}>邮箱</Text>
                <TextInput
                  value={email}
                  onChangeText={(v) => { setEmail(v); setLocalError(null); }}
                  autoCapitalize="none"
                  autoCorrect={false}
                  keyboardType="email-address"
                  placeholder="you@example.com"
                  placeholderTextColor={colors.text4}
                  style={styles.input}
                />
              </View>

              <View style={styles.field}>
                <Text style={styles.label}>密码</Text>
                <TextInput
                  value={password}
                  onChangeText={(v) => { setPassword(v); setLocalError(null); }}
                  secureTextEntry
                  placeholder="输入密码"
                  placeholderTextColor={colors.text4}
                  style={styles.input}
                />
              </View>

              {localError ? <Text style={styles.errorText}>{localError}</Text> : null}

              <Pressable
                style={[styles.loginBtn, loginMutation.isPending && styles.loginBtnDisabled]}
                disabled={loginMutation.isPending}
                onPress={() => void loginMutation.mutateAsync()}
              >
                {loginMutation.isPending ? (
                  <ActivityIndicator color="#fff" />
                ) : (
                  <Text style={styles.loginBtnText}>登录</Text>
                )}
              </Pressable>
            </View>

            {/* Server info */}
            <Text style={styles.serverHint}>{configHint}</Text>
          </View>
        </ScrollView>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safeArea: {
    flex: 1,
    backgroundColor: colors.bg,
  },
  keyboard: {
    flex: 1,
  },
  scrollContent: {
    flexGrow: 1,
    justifyContent: "center",
    paddingHorizontal: 24,
    paddingVertical: 28,
  },
  contentWrap: {
    width: "100%",
    maxWidth: 400,
    alignSelf: "center",
    gap: 28,
  },
  hero: {
    alignItems: "center",
    gap: 6,
  },
  logoBox: {
    width: 80,
    height: 80,
    borderRadius: 40,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: colors.primary,
    marginBottom: 8,
    ...shadowMd,
  },
  logoEmoji: {
    fontSize: 36,
  },
  brand: {
    fontSize: 32,
    fontWeight: "800",
    color: colors.text,
    letterSpacing: 2,
  },
  subtitle: {
    fontSize: 15,
    color: colors.text3,
    marginTop: 2,
  },
  card: {
    borderRadius: radii.lg,
    backgroundColor: colors.surface,
    padding: 22,
    gap: 18,
    ...shadowMd,
  },
  field: {
    gap: 8,
  },
  label: {
    fontSize: 13,
    fontWeight: "600",
    color: colors.text2,
  },
  input: {
    borderWidth: 1,
    borderColor: colors.borderLight,
    borderRadius: radii.md,
    backgroundColor: colors.bg,
    paddingHorizontal: 14,
    paddingVertical: 14,
    fontSize: 16,
    color: colors.text,
  },
  errorText: {
    fontSize: 14,
    color: colors.danger,
  },
  loginBtn: {
    alignItems: "center",
    justifyContent: "center",
    borderRadius: radii.md,
    paddingVertical: 16,
    backgroundColor: colors.primary,
    marginTop: 4,
  },
  loginBtnDisabled: {
    opacity: 0.6,
  },
  loginBtnText: {
    fontSize: 16,
    fontWeight: "700",
    color: "#fff",
  },
  serverHint: {
    textAlign: "center",
    fontSize: 12,
    color: colors.text4,
  },
});
