import { useEffect, useMemo, useState } from "react";
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

import {
  API_BASE,
  API_BASE_HELP,
  ActionResponse,
  loginWithCode,
  loginWithPassword,
  registerWithCode,
  resetPassword,
  sendAuthEmailCode,
  TokenResponse,
} from "../lib/api";
import { colors, radii, shadowMd } from "../design/tokens";
import { useAuthStore } from "../store/auth";

type AuthMode = "password" | "code" | "register" | "reset";

type SubmitResult =
  | { kind: "token"; data: TokenResponse }
  | { kind: "message"; data: ActionResponse };

const MODE_ITEMS: Array<{ key: AuthMode; label: string }> = [
  { key: "password", label: "密码登录" },
  { key: "code", label: "验证码登录" },
  { key: "register", label: "注册" },
  { key: "reset", label: "重置密码" },
];

const MODE_TITLES: Record<AuthMode, string> = {
  password: "邮箱密码登录",
  code: "邮箱验证码登录",
  register: "邮箱注册",
  reset: "重置登录密码",
};

const MODE_SUBTITLES: Record<AuthMode, string> = {
  password: "使用邮箱和密码直接登录你的账号。",
  code: "收邮件拿验证码，免输密码也能登录。",
  register: "先收验证码，再完成邮箱注册。",
  reset: "验证邮箱后设置一个新的登录密码。",
};

const PRIMARY_BUTTON_LABEL: Record<AuthMode, string> = {
  password: "登录",
  code: "验证码登录",
  register: "注册并登录",
  reset: "重置密码",
};

function validateEmail(email: string) {
  const trimmedEmail = email.trim();
  if (!trimmedEmail) return "请输入邮箱地址";
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(trimmedEmail)) return "请输入有效的邮箱地址";
  return null;
}

function validatePassword(password: string, fieldLabel = "密码") {
  if (!password.trim()) return `请输入${fieldLabel}`;
  if (password.trim().length < 6) return `${fieldLabel}至少 6 位`;
  return null;
}

function validateCode(code: string) {
  if (!/^\d{6}$/.test(code.trim())) return "请输入 6 位验证码";
  return null;
}

function getCodePurpose(mode: AuthMode): "login" | "register" | "reset_password" {
  if (mode === "register") return "register";
  if (mode === "reset") return "reset_password";
  return "login";
}

export function LoginScreen() {
  const setToken = useAuthStore((state) => state.setToken);
  const [mode, setMode] = useState<AuthMode>("password");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [code, setCode] = useState("");
  const [cooldownLeft, setCooldownLeft] = useState(0);
  const [localError, setLocalError] = useState<string | null>(null);
  const [localNotice, setLocalNotice] = useState<string | null>(null);

  const configHint = useMemo(() => {
    if (API_BASE) return API_BASE;
    return API_BASE_HELP;
  }, []);

  useEffect(() => {
    if (cooldownLeft <= 0) return;
    const timer = setTimeout(() => setCooldownLeft((prev) => Math.max(prev - 1, 0)), 1000);
    return () => clearTimeout(timer);
  }, [cooldownLeft]);

  const sendCodeMutation = useMutation({
    mutationFn: async () => {
      const emailError = validateEmail(email);
      if (emailError) throw new Error(emailError);
      return sendAuthEmailCode(email, getCodePurpose(mode));
    },
    onSuccess: (data) => {
      setLocalError(null);
      setLocalNotice(data.message);
      setCooldownLeft(Math.max(Number(data.cooldown_seconds || 60), 1));
    },
    onError: (error: Error) => {
      setLocalNotice(null);
      setLocalError(error.message);
    },
  });

  const submitMutation = useMutation({
    mutationFn: async (): Promise<SubmitResult> => {
      const emailError = validateEmail(email);
      if (emailError) throw new Error(emailError);

      if (mode === "password") {
        const passwordError = validatePassword(password);
        if (passwordError) throw new Error(passwordError);
        return { kind: "token", data: await loginWithPassword(email, password) };
      }

      const codeError = validateCode(code);
      if (codeError) throw new Error(codeError);

      if (mode === "code") {
        return { kind: "token", data: await loginWithCode(email, code) };
      }

      const passwordError = validatePassword(password, mode === "register" ? "登录密码" : "新密码");
      if (passwordError) throw new Error(passwordError);
      if (password !== confirmPassword) throw new Error("两次输入的密码不一致");

      if (mode === "register") {
        return {
          kind: "token",
          data: await registerWithCode(email, password, confirmPassword, code),
        };
      }

      return {
        kind: "message",
        data: await resetPassword(email, code, password, confirmPassword),
      };
    },
    onSuccess: async (result) => {
      setLocalError(null);
      if (result.kind === "token") {
        setLocalNotice(null);
        await setToken(result.data.access_token);
        return;
      }

      setLocalNotice(result.data.message);
      setMode("password");
      setPassword("");
      setConfirmPassword("");
      setCode("");
      setCooldownLeft(0);
    },
    onError: (error: Error) => {
      setLocalNotice(null);
      setLocalError(error.message);
    },
  });

  const showCodeFields = mode !== "password";
  const showConfirmPassword = mode === "register" || mode === "reset";
  const sendCodeDisabled =
    !!validateEmail(email) || cooldownLeft > 0 || sendCodeMutation.isPending || submitMutation.isPending;

  function switchMode(nextMode: AuthMode) {
    setMode(nextMode);
    setLocalError(null);
    setLocalNotice(null);
    setCooldownLeft(0);
    setCode("");
    if (nextMode === "code") {
      setPassword("");
      setConfirmPassword("");
    }
  }

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
                <Text style={styles.logoEmoji}>✨</Text>
              </View>
              <Text style={styles.brand}>PAI</Text>
              <Text style={styles.subtitle}>你的个人 AI 助手</Text>
            </View>

            <View style={styles.modeRow}>
              {MODE_ITEMS.map((item) => {
                const active = item.key === mode;
                return (
                  <Pressable
                    key={item.key}
                    style={[styles.modeChip, active && styles.modeChipActive]}
                    onPress={() => switchMode(item.key)}
                  >
                    <Text style={[styles.modeChipText, active && styles.modeChipTextActive]}>{item.label}</Text>
                  </Pressable>
                );
              })}
            </View>

            <View style={styles.card}>
              <View style={styles.cardHead}>
                <Text style={styles.cardTitle}>{MODE_TITLES[mode]}</Text>
                <Text style={styles.cardDesc}>{MODE_SUBTITLES[mode]}</Text>
              </View>

              <View style={styles.field}>
                <Text style={styles.label}>邮箱</Text>
                <TextInput
                  value={email}
                  onChangeText={(value) => {
                    setEmail(value);
                    setLocalError(null);
                    setLocalNotice(null);
                  }}
                  autoCapitalize="none"
                  autoCorrect={false}
                  keyboardType="email-address"
                  placeholder="you@example.com"
                  placeholderTextColor={colors.text4}
                  style={styles.input}
                />
              </View>

              {showCodeFields ? (
                <View style={styles.field}>
                  <Text style={styles.label}>验证码</Text>
                  <View style={styles.codeRow}>
                    <TextInput
                      value={code}
                      onChangeText={(value) => {
                        setCode(value.replace(/\D+/g, "").slice(0, 6));
                        setLocalError(null);
                        setLocalNotice(null);
                      }}
                      keyboardType="number-pad"
                      placeholder="请输入 6 位验证码"
                      placeholderTextColor={colors.text4}
                      style={[styles.input, styles.codeInput]}
                    />
                    <Pressable
                      style={[styles.codeBtn, sendCodeDisabled && styles.codeBtnDisabled]}
                      disabled={sendCodeDisabled}
                      onPress={() => void sendCodeMutation.mutateAsync()}
                    >
                      <Text style={styles.codeBtnText}>
                        {sendCodeMutation.isPending
                          ? "发送中"
                          : cooldownLeft > 0
                            ? `${cooldownLeft}s`
                            : "发送验证码"}
                      </Text>
                    </Pressable>
                  </View>
                </View>
              ) : null}

              {mode !== "code" ? (
                <View style={styles.field}>
                  <Text style={styles.label}>{mode === "reset" ? "新密码" : "密码"}</Text>
                  <TextInput
                    value={password}
                    onChangeText={(value) => {
                      setPassword(value);
                      setLocalError(null);
                      setLocalNotice(null);
                    }}
                    secureTextEntry
                    placeholder={mode === "reset" ? "输入新的登录密码" : "输入密码"}
                    placeholderTextColor={colors.text4}
                    style={styles.input}
                  />
                </View>
              ) : null}

              {showConfirmPassword ? (
                <View style={styles.field}>
                  <Text style={styles.label}>确认密码</Text>
                  <TextInput
                    value={confirmPassword}
                    onChangeText={(value) => {
                      setConfirmPassword(value);
                      setLocalError(null);
                      setLocalNotice(null);
                    }}
                    secureTextEntry
                    placeholder="再次输入密码"
                    placeholderTextColor={colors.text4}
                    style={styles.input}
                  />
                </View>
              ) : null}

              {localError ? <Text style={styles.errorText}>{localError}</Text> : null}
              {localNotice ? <Text style={styles.noticeText}>{localNotice}</Text> : null}

              <Pressable
                style={[styles.loginBtn, submitMutation.isPending && styles.loginBtnDisabled]}
                disabled={submitMutation.isPending}
                onPress={() => void submitMutation.mutateAsync()}
              >
                {submitMutation.isPending ? (
                  <ActivityIndicator color="#fff" />
                ) : (
                  <Text style={styles.loginBtnText}>{PRIMARY_BUTTON_LABEL[mode]}</Text>
                )}
              </Pressable>
            </View>

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
    maxWidth: 420,
    alignSelf: "center",
    gap: 22,
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
  modeRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8,
  },
  modeChip: {
    paddingHorizontal: 14,
    paddingVertical: 10,
    borderRadius: radii.full,
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.borderLight,
  },
  modeChipActive: {
    backgroundColor: colors.primaryLight,
    borderColor: "rgba(79,110,247,0.2)",
  },
  modeChipText: {
    fontSize: 13,
    fontWeight: "700",
    color: colors.text2,
  },
  modeChipTextActive: {
    color: colors.primaryDark,
  },
  card: {
    borderRadius: radii.lg,
    backgroundColor: colors.surface,
    padding: 22,
    gap: 16,
    ...shadowMd,
  },
  cardHead: {
    gap: 6,
  },
  cardTitle: {
    fontSize: 20,
    fontWeight: "800",
    color: colors.text,
  },
  cardDesc: {
    fontSize: 13,
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
    borderRadius: radii.md,
    backgroundColor: colors.bg,
    paddingHorizontal: 14,
    paddingVertical: 14,
    fontSize: 16,
    color: colors.text,
  },
  codeRow: {
    flexDirection: "row",
    gap: 10,
  },
  codeInput: {
    flex: 1,
  },
  codeBtn: {
    minWidth: 108,
    alignItems: "center",
    justifyContent: "center",
    borderRadius: radii.md,
    backgroundColor: colors.primary,
    paddingHorizontal: 12,
  },
  codeBtnDisabled: {
    opacity: 0.5,
  },
  codeBtnText: {
    fontSize: 13,
    fontWeight: "700",
    color: "#ffffff",
  },
  errorText: {
    fontSize: 14,
    lineHeight: 20,
    color: colors.danger,
  },
  noticeText: {
    fontSize: 14,
    lineHeight: 20,
    color: colors.primaryDark,
    backgroundColor: colors.primaryLight,
    borderRadius: radii.md,
    paddingHorizontal: 12,
    paddingVertical: 10,
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
