import { useEffect, useMemo, useRef, useState } from "react";
import {
  ActivityIndicator,
  Animated,
  Dimensions,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { Ionicons } from "@expo/vector-icons";
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
import { colors, radii, shadowLg, shadowMd, shadowSm } from "../design/tokens";
import { useAuthStore } from "../store/auth";

type AuthMode = "password" | "code" | "register" | "reset";

type SubmitResult =
  | { kind: "token"; data: TokenResponse }
  | { kind: "message"; data: ActionResponse };

const MODE_ITEMS: Array<{ key: AuthMode; label: string; icon: keyof typeof Ionicons.glyphMap }> = [
  { key: "password", label: "密码", icon: "lock-closed-outline" },
  { key: "code", label: "验证码", icon: "mail-outline" },
  { key: "register", label: "注册", icon: "person-add-outline" },
  { key: "reset", label: "重置", icon: "refresh-outline" },
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

const { width: SCREEN_WIDTH } = Dimensions.get("window");

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

  /* ── Entrance animations ── */
  const fadeIn = useRef(new Animated.Value(0)).current;
  const slideUp = useRef(new Animated.Value(40)).current;
  const logoScale = useRef(new Animated.Value(0.8)).current;
  const logoRotate = useRef(new Animated.Value(0)).current;
  const orb1 = useRef(new Animated.Value(0)).current;
  const orb2 = useRef(new Animated.Value(0)).current;

  useEffect(() => {
    // Logo entrance
    Animated.parallel([
      Animated.spring(logoScale, { toValue: 1, friction: 5, tension: 80, useNativeDriver: true }),
      Animated.timing(fadeIn, { toValue: 1, duration: 600, useNativeDriver: true }),
      Animated.timing(slideUp, { toValue: 0, duration: 600, useNativeDriver: true }),
    ]).start();

    // Continuous floating orbs
    Animated.loop(
      Animated.sequence([
        Animated.timing(orb1, { toValue: 1, duration: 4000, useNativeDriver: true }),
        Animated.timing(orb1, { toValue: 0, duration: 4000, useNativeDriver: true }),
      ])
    ).start();
    Animated.loop(
      Animated.sequence([
        Animated.timing(orb2, { toValue: 1, duration: 3200, useNativeDriver: true }),
        Animated.timing(orb2, { toValue: 0, duration: 3200, useNativeDriver: true }),
      ])
    ).start();

    // Logo breathing
    Animated.loop(
      Animated.sequence([
        Animated.timing(logoRotate, { toValue: 1, duration: 3000, useNativeDriver: true }),
        Animated.timing(logoRotate, { toValue: 0, duration: 3000, useNativeDriver: true }),
      ])
    ).start();
  }, [fadeIn, slideUp, logoScale, logoRotate, orb1, orb2]);

  const orb1TranslateY = orb1.interpolate({ inputRange: [0, 1], outputRange: [0, -18] });
  const orb2TranslateX = orb2.interpolate({ inputRange: [0, 1], outputRange: [0, 14] });
  const logoScaleBreath = logoRotate.interpolate({ inputRange: [0, 1], outputRange: [1, 1.05] });

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
    <View style={styles.root}>
      {/* ── Decorative background orbs ── */}
      <Animated.View style={[styles.orbOne, { transform: [{ translateY: orb1TranslateY }] }]} />
      <Animated.View style={[styles.orbTwo, { transform: [{ translateX: orb2TranslateX }] }]} />
      <View style={styles.orbThree} />

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
            <Animated.View style={[styles.contentWrap, { opacity: fadeIn, transform: [{ translateY: slideUp }] }]}>
              {/* ── Hero ── */}
              <View style={styles.hero}>
                <Animated.View style={[styles.logoOuter, { transform: [{ scale: Animated.multiply(logoScale, logoScaleBreath) }] }]}>
                  <View style={styles.logoRing}>
                    <View style={styles.logoBox}>
                      <Text style={styles.logoEmoji}>✨</Text>
                    </View>
                  </View>
                </Animated.View>
                <Text style={styles.brand}>PAI</Text>
                <Text style={styles.subtitle}>你的个人 AI 助手</Text>
              </View>

              {/* ── Mode selector ── */}
              <View style={styles.modeRow}>
                {MODE_ITEMS.map((item) => {
                  const active = item.key === mode;
                  return (
                    <Pressable
                      key={item.key}
                      style={[styles.modeChip, active && styles.modeChipActive]}
                      onPress={() => switchMode(item.key)}
                    >
                      <Ionicons
                        name={item.icon}
                        size={14}
                        color={active ? colors.primary : colors.text3}
                        style={{ marginRight: 5 }}
                      />
                      <Text style={[styles.modeChipText, active && styles.modeChipTextActive]}>{item.label}</Text>
                    </Pressable>
                  );
                })}
              </View>

              {/* ── Form card ── */}
              <View style={styles.card}>
                <View style={styles.cardHead}>
                  <Text style={styles.cardTitle}>{MODE_TITLES[mode]}</Text>
                  <Text style={styles.cardDesc}>{MODE_SUBTITLES[mode]}</Text>
                </View>

                <View style={styles.field}>
                  <Text style={styles.label}>邮箱</Text>
                  <View style={styles.inputWrap}>
                    <Ionicons name="mail-outline" size={18} color={colors.text4} style={styles.inputIcon} />
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
                </View>

                {showCodeFields ? (
                  <View style={styles.field}>
                    <Text style={styles.label}>验证码</Text>
                    <View style={styles.codeRow}>
                      <View style={[styles.inputWrap, { flex: 1 }]}>
                        <Ionicons name="keypad-outline" size={18} color={colors.text4} style={styles.inputIcon} />
                        <TextInput
                          value={code}
                          onChangeText={(value) => {
                            setCode(value.replace(/\D+/g, "").slice(0, 6));
                            setLocalError(null);
                            setLocalNotice(null);
                          }}
                          keyboardType="number-pad"
                          placeholder="6 位验证码"
                          placeholderTextColor={colors.text4}
                          style={styles.input}
                        />
                      </View>
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
                              : "发送"}
                        </Text>
                      </Pressable>
                    </View>
                  </View>
                ) : null}

                {mode !== "code" ? (
                  <View style={styles.field}>
                    <Text style={styles.label}>{mode === "reset" ? "新密码" : "密码"}</Text>
                    <View style={styles.inputWrap}>
                      <Ionicons name="lock-closed-outline" size={18} color={colors.text4} style={styles.inputIcon} />
                      <TextInput
                        value={password}
                        onChangeText={(value) => {
                          setPassword(value);
                          setLocalError(null);
                          setLocalNotice(null);
                        }}
                        secureTextEntry
                        placeholder={mode === "reset" ? "输入新密码" : "输入密码"}
                        placeholderTextColor={colors.text4}
                        style={styles.input}
                      />
                    </View>
                  </View>
                ) : null}

                {showConfirmPassword ? (
                  <View style={styles.field}>
                    <Text style={styles.label}>确认密码</Text>
                    <View style={styles.inputWrap}>
                      <Ionicons name="shield-checkmark-outline" size={18} color={colors.text4} style={styles.inputIcon} />
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
                  </View>
                ) : null}

                {localError ? (
                  <View style={styles.errorBox}>
                    <Ionicons name="alert-circle" size={16} color={colors.danger} />
                    <Text style={styles.errorText}>{localError}</Text>
                  </View>
                ) : null}
                {localNotice ? (
                  <View style={styles.noticeBox}>
                    <Ionicons name="checkmark-circle" size={16} color={colors.primary} />
                    <Text style={styles.noticeText}>{localNotice}</Text>
                  </View>
                ) : null}

                <Pressable
                  style={[styles.loginBtn, submitMutation.isPending && styles.loginBtnDisabled]}
                  disabled={submitMutation.isPending}
                  onPress={() => void submitMutation.mutateAsync()}
                >
                  {submitMutation.isPending ? (
                    <ActivityIndicator color="#fff" />
                  ) : (
                    <>
                      <Text style={styles.loginBtnText}>{PRIMARY_BUTTON_LABEL[mode]}</Text>
                      <Ionicons name="arrow-forward" size={18} color="#fff" style={{ marginLeft: 6 }} />
                    </>
                  )}
                </Pressable>
              </View>

              <Text style={styles.serverHint}>{configHint}</Text>
            </Animated.View>
          </ScrollView>
        </KeyboardAvoidingView>
      </SafeAreaView>
    </View>
  );
}

const styles = StyleSheet.create({
  root: {
    flex: 1,
    backgroundColor: colors.bg,
  },
  safeArea: {
    flex: 1,
  },
  keyboard: {
    flex: 1,
  },

  /* ── Decorative orbs ── */
  orbOne: {
    position: "absolute",
    top: -60,
    right: -40,
    width: SCREEN_WIDTH * 0.55,
    height: SCREEN_WIDTH * 0.55,
    borderRadius: SCREEN_WIDTH * 0.275,
    backgroundColor: "rgba(79,110,247,0.08)",
  },
  orbTwo: {
    position: "absolute",
    bottom: "12%",
    left: -50,
    width: SCREEN_WIDTH * 0.5,
    height: SCREEN_WIDTH * 0.5,
    borderRadius: SCREEN_WIDTH * 0.25,
    backgroundColor: "rgba(139,92,246,0.06)",
  },
  orbThree: {
    position: "absolute",
    top: "38%",
    right: -20,
    width: 120,
    height: 120,
    borderRadius: 60,
    backgroundColor: "rgba(16,185,129,0.05)",
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
    gap: 24,
  },

  /* ── Hero ── */
  hero: {
    alignItems: "center",
    gap: 8,
    marginBottom: 4,
  },
  logoOuter: {
    marginBottom: 6,
  },
  logoRing: {
    width: 100,
    height: 100,
    borderRadius: 50,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: "rgba(79,110,247,0.08)",
    borderWidth: 2,
    borderColor: "rgba(79,110,247,0.12)",
  },
  logoBox: {
    width: 72,
    height: 72,
    borderRadius: 36,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: colors.primary,
    ...shadowLg,
  },
  logoEmoji: {
    fontSize: 34,
  },
  brand: {
    fontSize: 36,
    fontWeight: "900",
    color: colors.text,
    letterSpacing: 4,
  },
  subtitle: {
    fontSize: 15,
    color: colors.text3,
    letterSpacing: 0.5,
  },

  /* ── Mode selector ── */
  modeRow: {
    flexDirection: "row",
    justifyContent: "center",
    gap: 8,
  },
  modeChip: {
    flexDirection: "row",
    alignItems: "center",
    paddingHorizontal: 14,
    paddingVertical: 10,
    borderRadius: radii.full,
    backgroundColor: colors.surface,
    borderWidth: 1.5,
    borderColor: colors.borderLight,
  },
  modeChipActive: {
    backgroundColor: colors.primaryLight,
    borderColor: "rgba(79,110,247,0.25)",
    ...shadowSm,
  },
  modeChipText: {
    fontSize: 13,
    fontWeight: "700",
    color: colors.text3,
  },
  modeChipTextActive: {
    color: colors.primary,
  },

  /* ── Form card ── */
  card: {
    borderRadius: radii.xl,
    backgroundColor: colors.surface,
    padding: 24,
    gap: 18,
    ...shadowLg,
  },
  cardHead: {
    gap: 6,
    marginBottom: 2,
  },
  cardTitle: {
    fontSize: 22,
    fontWeight: "800",
    color: colors.text,
  },
  cardDesc: {
    fontSize: 14,
    lineHeight: 21,
    color: colors.text3,
  },
  field: {
    gap: 8,
  },
  label: {
    fontSize: 13,
    fontWeight: "700",
    color: colors.text2,
    marginLeft: 2,
  },
  inputWrap: {
    flexDirection: "row",
    alignItems: "center",
    borderWidth: 1.5,
    borderColor: colors.border,
    borderRadius: radii.md,
    backgroundColor: colors.bg,
    paddingHorizontal: 14,
  },
  inputIcon: {
    marginRight: 10,
  },
  input: {
    flex: 1,
    paddingVertical: 14,
    fontSize: 16,
    color: colors.text,
  },
  codeRow: {
    flexDirection: "row",
    gap: 10,
  },
  codeBtn: {
    minWidth: 80,
    alignItems: "center",
    justifyContent: "center",
    borderRadius: radii.md,
    backgroundColor: colors.primary,
    paddingHorizontal: 16,
    ...shadowSm,
  },
  codeBtnDisabled: {
    opacity: 0.5,
  },
  codeBtnText: {
    fontSize: 14,
    fontWeight: "700",
    color: "#ffffff",
  },

  /* ── Feedback ── */
  errorBox: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    backgroundColor: colors.dangerLight,
    borderRadius: radii.sm,
    paddingHorizontal: 14,
    paddingVertical: 12,
  },
  errorText: {
    flex: 1,
    fontSize: 14,
    lineHeight: 20,
    color: colors.danger,
  },
  noticeBox: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    backgroundColor: colors.primaryLight,
    borderRadius: radii.sm,
    paddingHorizontal: 14,
    paddingVertical: 12,
  },
  noticeText: {
    flex: 1,
    fontSize: 14,
    lineHeight: 20,
    color: colors.primaryDark,
  },

  /* ── Submit ── */
  loginBtn: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    borderRadius: radii.lg,
    paddingVertical: 16,
    backgroundColor: colors.primary,
    marginTop: 4,
    ...shadowMd,
  },
  loginBtnDisabled: {
    opacity: 0.6,
  },
  loginBtnText: {
    fontSize: 17,
    fontWeight: "800",
    color: "#fff",
  },
  serverHint: {
    textAlign: "center",
    fontSize: 12,
    color: colors.text4,
  },
});
