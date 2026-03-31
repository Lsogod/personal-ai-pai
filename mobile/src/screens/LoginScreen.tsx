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
  if (!trimmedEmail) return "请输入邮箱地址。";
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(trimmedEmail)) return "请输入有效的邮箱地址。";
  if (!password.trim()) return "请输入密码。";
  return null;
}

export function LoginScreen() {
  const setToken = useAuthStore((state) => state.setToken);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [localError, setLocalError] = useState<string | null>(null);

  const configHint = useMemo(() => {
    if (API_BASE) {
      return `当前后端：${API_BASE}`;
    }
    return API_BASE_HELP;
  }, []);

  const loginMutation = useMutation({
    mutationFn: async () => {
      const error = validate(email, password);
      if (error) {
        throw new Error(error);
      }
      return loginWithPassword(email, password);
    },
    onSuccess: async (data) => {
      setLocalError(null);
      await setToken(data.access_token);
    },
    onError: (error: Error) => {
      setLocalError(error.message);
    },
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
            <View style={styles.hero}>
              <View style={styles.logoBox}>
                <Text style={styles.logoText}>PAI</Text>
              </View>
              <Text style={styles.brand}>原生移动客户端</Text>
              <Text style={styles.subtitle}>登录后可多端同步数据，界面结构按现有小程序重做。</Text>
            </View>

            <View style={styles.card}>
              <Text style={styles.cardTitle}>邮箱登录</Text>
              <Text style={styles.cardHint}>登录方式沿用 Web 端邮箱体系，但界面和导航按小程序风格组织。</Text>

              <View style={styles.field}>
                <Text style={styles.label}>邮箱</Text>
                <TextInput
                  value={email}
                  onChangeText={(value) => {
                    setEmail(value);
                    setLocalError(null);
                  }}
                  autoCapitalize="none"
                  autoCorrect={false}
                  keyboardType="email-address"
                  placeholder="you@example.com"
                  placeholderTextColor="#94a3b8"
                  style={styles.input}
                />
              </View>

              <View style={styles.field}>
                <Text style={styles.label}>密码</Text>
                <TextInput
                  value={password}
                  onChangeText={(value) => {
                    setPassword(value);
                    setLocalError(null);
                  }}
                  secureTextEntry
                  placeholder="请输入密码"
                  placeholderTextColor="#94a3b8"
                  style={styles.input}
                />
              </View>

              {!!localError && <Text style={styles.errorText}>{localError}</Text>}

              <Pressable
                style={[styles.primaryButton, loginMutation.isPending && styles.primaryButtonDisabled]}
                disabled={loginMutation.isPending}
                onPress={() => void loginMutation.mutateAsync()}
              >
                {loginMutation.isPending ? (
                  <ActivityIndicator color="#ffffff" />
                ) : (
                  <Text style={styles.primaryButtonText}>登录</Text>
                )}
              </Pressable>

              <View style={styles.configBox}>
                <Text style={styles.configLabel}>接口配置</Text>
                <Text style={styles.configText}>{configHint}</Text>
              </View>
            </View>
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
    paddingHorizontal: 20,
    paddingVertical: 28,
  },
  contentWrap: {
    width: "100%",
    maxWidth: 520,
    alignSelf: "center",
    gap: 24,
  },
  hero: {
    alignItems: "center",
    gap: 8,
  },
  logoBox: {
    width: 88,
    height: 88,
    borderRadius: radii.xl,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: colors.primary,
    marginBottom: 10,
    ...shadowMd,
  },
  logoText: {
    fontSize: 30,
    fontWeight: "800",
    letterSpacing: 1,
    color: "#ffffff",
  },
  brand: {
    fontSize: 28,
    fontWeight: "800",
    color: colors.text,
  },
  subtitle: {
    fontSize: 14,
    lineHeight: 20,
    textAlign: "center",
    color: colors.text3,
  },
  card: {
    borderRadius: radii.lg,
    backgroundColor: colors.surface,
    padding: 20,
    gap: 16,
    width: "100%",
    ...shadowMd,
  },
  cardTitle: {
    fontSize: 22,
    fontWeight: "700",
    color: colors.text,
  },
  cardHint: {
    fontSize: 14,
    lineHeight: 20,
    color: colors.text3,
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
    borderRadius: radii.lg,
    backgroundColor: colors.bg,
    paddingHorizontal: 14,
    paddingVertical: 14,
    fontSize: 16,
    color: colors.text,
  },
  errorText: {
    fontSize: 14,
    lineHeight: 20,
    color: colors.danger,
  },
  primaryButton: {
    alignItems: "center",
    justifyContent: "center",
    borderRadius: radii.md,
    paddingVertical: 15,
    backgroundColor: colors.primary,
  },
  primaryButtonDisabled: {
    opacity: 0.65,
  },
  primaryButtonText: {
    fontSize: 16,
    fontWeight: "700",
    color: "#ffffff",
  },
  configBox: {
    borderRadius: radii.md,
    backgroundColor: colors.primaryLight,
    padding: 14,
    gap: 6,
  },
  configLabel: {
    fontSize: 12,
    fontWeight: "700",
    color: colors.primaryDark,
    textTransform: "uppercase",
  },
  configText: {
    fontSize: 13,
    lineHeight: 18,
    color: colors.primaryDark,
  },
});
