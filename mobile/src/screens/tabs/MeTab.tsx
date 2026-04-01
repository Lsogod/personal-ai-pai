import { Alert, Animated, Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Ionicons } from "@expo/vector-icons";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { fetchConversations, fetchProfile, fetchSkills, getSourcePlatformLabel } from "../../lib/api";
import { useAuthStore } from "../../store/auth";
import { colors, radii, shadowLg, shadowMd, shadowSm, spacing, surfaceCard } from "../../design/tokens";
import type { TabKey } from "../../components/MiniTabBar";
import { BindingPanel } from "../me/BindingPanel";
import { FeedbackPanel } from "../me/FeedbackPanel";
import { SkillsPanel } from "../me/SkillsPanel";

type MeTabProps = {
  bottomInset: number;
  onNavigate: (tab: TabKey) => void;
  onLogout: () => void | Promise<void>;
};

const MENU_ITEMS = [
  { key: "skills", title: "技能管理", desc: "查看和管理 AI 技能", icon: "sparkles-outline" as const, color: colors.iconBgPurple, iconColor: "#9333ea" },
  { key: "binding", title: "账号绑定", desc: "多平台数据同步", icon: "link-outline" as const, color: colors.iconBgPrimary, iconColor: colors.primary },
  { key: "notifications", title: "推送通知", desc: "日程提醒推送设置", icon: "notifications-outline" as const, color: colors.iconBgOrange, iconColor: colors.warning },
  { key: "feedback", title: "问题反馈", desc: "帮助我们改进", icon: "chatbubble-ellipses-outline" as const, color: colors.iconBgGreen, iconColor: colors.accent },
] as const;

export function MeTab({ bottomInset, onNavigate, onLogout }: MeTabProps) {
  const token = useAuthStore((state) => state.token);
  const insets = useSafeAreaInsets();
  const [panel, setPanel] = useState<"skills" | "binding" | "feedback" | null>(null);
  const fadeIn = useRef(new Animated.Value(0)).current;
  const slideUp = useRef(new Animated.Value(30)).current;

  useEffect(() => {
    Animated.parallel([
      Animated.timing(fadeIn, { toValue: 1, duration: 500, useNativeDriver: true }),
      Animated.spring(slideUp, { toValue: 0, friction: 8, tension: 50, useNativeDriver: true }),
    ]).start();
  }, [fadeIn, slideUp]);

  const profileQuery = useQuery({
    queryKey: ["profile"],
    enabled: !!token,
    queryFn: () => fetchProfile(token!),
  });

  const skillsQuery = useQuery({
    queryKey: ["skills", "summary"],
    enabled: !!token,
    queryFn: () => fetchSkills(token!),
  });

  const conversationsQuery = useQuery({
    queryKey: ["conversations", "summary"],
    enabled: !!token,
    queryFn: () => fetchConversations(token!),
  });

  const profile = profileQuery.data;
  const activeSkillCount = (skillsQuery.data || []).filter((item) => item.status === "active").length;
  const conversationCount = (conversationsQuery.data || []).length;
  const setupProgress = Math.min(Math.max(Number(profile?.setup_stage || 0), 1), 3);
  const progressWidth = `${Math.max(18, Math.min(100, (setupProgress / 3) * 100))}%` as `${number}%`;
  const platformLabel = getSourcePlatformLabel(profile?.platform);

  function handleMenuPress(item: (typeof MENU_ITEMS)[number]) {
    if (item.key === "skills" || item.key === "binding" || item.key === "feedback") {
      setPanel(item.key);
      return;
    }
    Alert.alert("推送通知", "原生推送功能正在接入，后续这里会补推送权限、静默时段和提醒样式设置。");
  }

  return (
    <>
      <ScrollView
        style={styles.page}
        contentContainerStyle={{ paddingBottom: bottomInset + 24 }}
        showsVerticalScrollIndicator={false}
      >
        <Animated.View style={[styles.inner, { paddingTop: insets.top + 8, opacity: fadeIn, transform: [{ translateY: slideUp }] }]}>
          {/* ── Profile Hero ── */}
          <View style={styles.profileCard}>
            <View style={styles.heroGlowOne} />
            <View style={styles.heroGlowTwo} />
            <View style={styles.heroGlowThree} />
            <View style={styles.profileTop}>
              <View style={styles.avatarOuter}>
                <View style={styles.avatarRing}>
                  <Ionicons name="person" size={28} color="#fff" />
                </View>
              </View>
              <View style={styles.profileInfo}>
                <Text style={styles.profileName}>{profile?.nickname || "用户"}</Text>
                <Text style={styles.profileMeta}>
                  {profile?.email || platformLabel || "PAI 用户"}
                </Text>
              </View>
              <Pressable style={styles.profileAction} onPress={() => onNavigate("chat")}>
                <Ionicons name="sparkles" size={18} color="#fff" />
              </Pressable>
            </View>

            <View style={styles.progressWrap}>
              <View style={styles.progressLabelRow}>
                <Text style={styles.progressText}>账号完成度</Text>
                <Text style={styles.progressText}>{setupProgress}/3</Text>
              </View>
              <View style={styles.progressTrack}>
                <View style={[styles.progressFill, { width: progressWidth }]} />
              </View>
            </View>

            <View style={styles.profileStats}>
              <View style={styles.profileStat}>
                <Text style={styles.profileStatValue}>{activeSkillCount}</Text>
                <Text style={styles.profileStatLabel}>启用技能</Text>
              </View>
              <View style={styles.statDivider} />
              <View style={styles.profileStat}>
                <Text style={styles.profileStatValue}>{conversationCount}</Text>
                <Text style={styles.profileStatLabel}>对话数</Text>
              </View>
              <View style={styles.statDivider} />
              <View style={styles.profileStat}>
                <Text style={styles.profileStatValue}>{platformLabel || "云端"}</Text>
                <Text style={styles.profileStatLabel}>当前端</Text>
              </View>
            </View>
          </View>

          {/* ── Quick Actions ── */}
          <View style={styles.quickPanel}>
            <Pressable style={styles.quickCard} onPress={() => onNavigate("chat")}>
              <View style={[styles.quickIconWrap, { backgroundColor: colors.primaryLight }]}>
                <Ionicons name="sparkles-outline" size={20} color={colors.primary} />
              </View>
              <Text style={styles.quickTitle}>继续对话</Text>
              <Text style={styles.quickDesc}>直接打开助手</Text>
            </Pressable>
            <Pressable style={styles.quickCard} onPress={() => onNavigate("ledger")}>
              <View style={[styles.quickIconWrap, { backgroundColor: colors.accentLight }]}>
                <Ionicons name="wallet-outline" size={20} color={colors.accent} />
              </View>
              <Text style={styles.quickTitle}>查看账单</Text>
              <Text style={styles.quickDesc}>月度支出与明细</Text>
            </Pressable>
            <Pressable style={styles.quickCard} onPress={() => onNavigate("calendar")}>
              <View style={[styles.quickIconWrap, { backgroundColor: colors.warningLight }]}>
                <Ionicons name="calendar-outline" size={20} color={colors.warning} />
              </View>
              <Text style={styles.quickTitle}>提醒安排</Text>
              <Text style={styles.quickDesc}>管理待办与日程</Text>
            </Pressable>
          </View>

          {/* ── Account summary ── */}
          <View style={styles.summaryCard}>
            <View style={styles.summaryHead}>
              <View style={styles.summaryTitleRow}>
                <View style={styles.summaryDot} />
                <Text style={styles.summaryTitle}>账号状态</Text>
              </View>
              <View style={styles.summaryBadgeWrap}>
                <View style={styles.summaryBadgeDot} />
                <Text style={styles.summaryBadge}>已同步</Text>
              </View>
            </View>
            <View style={styles.summaryDivider} />
            <View style={styles.summaryRow}>
              <Ionicons name="mail-outline" size={16} color={colors.text3} />
              <Text style={styles.summaryLabel}>主账号</Text>
              <Text style={styles.summaryValue}>{profile?.email || "未绑定邮箱"}</Text>
            </View>
            <View style={styles.summaryRow}>
              <Ionicons name="cloud-outline" size={16} color={colors.text3} />
              <Text style={styles.summaryLabel}>数据来源</Text>
              <Text style={styles.summaryValue}>{platformLabel || "Web"}</Text>
            </View>
            <View style={styles.summaryRow}>
              <Ionicons name="bulb-outline" size={16} color={colors.text3} />
              <Text style={styles.summaryLabel}>建议下一步</Text>
              <Text style={[styles.summaryValue, styles.summaryValueHighlight]}>
                {activeSkillCount > 0 ? "补推送设置" : "先启用技能"}
              </Text>
            </View>
          </View>

          {/* ── Menu ── */}
          <View style={styles.menuCard}>
            {MENU_ITEMS.map((item, index) => (
              <View key={item.key}>
                <Pressable style={styles.menuRow} onPress={() => handleMenuPress(item)}>
                  <View style={[styles.menuIconWrap, { backgroundColor: item.color }]}>
                    <Ionicons name={item.icon} size={20} color={item.iconColor} />
                  </View>
                  <View style={styles.menuTexts}>
                    <Text style={styles.menuLabel}>{item.title}</Text>
                    <Text style={styles.menuDesc}>{item.desc}</Text>
                  </View>
                  <View style={styles.menuArrow}>
                    <Ionicons name="chevron-forward" size={16} color={colors.text4} />
                  </View>
                </Pressable>
                {index < MENU_ITEMS.length - 1 ? <View style={styles.divider} /> : null}
              </View>
            ))}
          </View>

          {/* ── Logout ── */}
          <Pressable style={styles.logoutBtn} onPress={() => void onLogout()}>
            <Ionicons name="log-out-outline" size={18} color={colors.danger} />
            <Text style={styles.logoutText}>退出登录</Text>
          </Pressable>
        </Animated.View>
      </ScrollView>

      <SkillsPanel visible={panel === "skills"} token={token} onClose={() => setPanel(null)} />
      <BindingPanel visible={panel === "binding"} token={token} onClose={() => setPanel(null)} />
      <FeedbackPanel visible={panel === "feedback"} token={token} onClose={() => setPanel(null)} />
    </>
  );
}

const styles = StyleSheet.create({
  page: {
    flex: 1,
    backgroundColor: colors.bg,
  },
  inner: {
    paddingHorizontal: spacing.pageX,
    gap: 16,
  },

  /* ── Profile card ── */
  profileCard: {
    overflow: "hidden",
    padding: 24,
    borderRadius: radii.xl,
    backgroundColor: colors.primary,
    gap: 20,
    ...shadowLg,
  },
  heroGlowOne: {
    position: "absolute",
    top: -32,
    right: -22,
    width: 140,
    height: 140,
    borderRadius: 999,
    backgroundColor: "rgba(255,255,255,0.14)",
  },
  heroGlowTwo: {
    position: "absolute",
    bottom: -48,
    left: -24,
    width: 150,
    height: 150,
    borderRadius: 999,
    backgroundColor: "rgba(255,255,255,0.08)",
  },
  heroGlowThree: {
    position: "absolute",
    top: "40%",
    right: "25%",
    width: 80,
    height: 80,
    borderRadius: 999,
    backgroundColor: "rgba(139,92,246,0.12)",
  },
  profileTop: {
    flexDirection: "row",
    alignItems: "center",
    gap: 16,
  },
  avatarOuter: {
    width: 68,
    height: 68,
    borderRadius: 34,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: "rgba(255,255,255,0.1)",
  },
  avatarRing: {
    width: 58,
    height: 58,
    borderRadius: 29,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: "rgba(255,255,255,0.22)",
    borderWidth: 2.5,
    borderColor: "rgba(255,255,255,0.4)",
  },
  profileInfo: {
    flex: 1,
    gap: 4,
  },
  profileName: {
    fontSize: 24,
    fontWeight: "800",
    color: "#fff",
  },
  profileMeta: {
    fontSize: 14,
    color: "rgba(255,255,255,0.72)",
  },
  profileAction: {
    width: 42,
    height: 42,
    borderRadius: 21,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: "rgba(255,255,255,0.16)",
    borderWidth: 1,
    borderColor: "rgba(255,255,255,0.2)",
  },

  /* Progress */
  progressWrap: {
    gap: 8,
  },
  progressLabelRow: {
    flexDirection: "row",
    justifyContent: "space-between",
  },
  progressTrack: {
    height: 8,
    borderRadius: 999,
    backgroundColor: "rgba(255,255,255,0.16)",
    overflow: "hidden",
  },
  progressFill: {
    height: "100%",
    borderRadius: 999,
    backgroundColor: "#fff",
  },
  progressText: {
    fontSize: 12,
    fontWeight: "700",
    color: "rgba(255,255,255,0.78)",
  },

  /* Stats row */
  profileStats: {
    flexDirection: "row",
    alignItems: "center",
    paddingVertical: 14,
    borderRadius: radii.md,
    backgroundColor: "rgba(255,255,255,0.1)",
  },
  profileStat: {
    flex: 1,
    alignItems: "center",
    gap: 4,
  },
  statDivider: {
    width: 1,
    height: 32,
    backgroundColor: "rgba(255,255,255,0.16)",
  },
  profileStatValue: {
    fontSize: 20,
    fontWeight: "800",
    color: "#fff",
  },
  profileStatLabel: {
    fontSize: 12,
    color: "rgba(255,255,255,0.68)",
  },

  /* ── Quick actions ── */
  quickPanel: {
    flexDirection: "row",
    gap: 10,
  },
  quickCard: {
    flex: 1,
    paddingVertical: 18,
    paddingHorizontal: 12,
    gap: 8,
    alignItems: "center",
    ...surfaceCard,
  },
  quickIconWrap: {
    width: 42,
    height: 42,
    borderRadius: 21,
    alignItems: "center",
    justifyContent: "center",
    marginBottom: 2,
  },
  quickTitle: {
    fontSize: 14,
    fontWeight: "700",
    color: colors.text,
  },
  quickDesc: {
    fontSize: 11,
    lineHeight: 16,
    color: colors.text3,
    textAlign: "center",
  },

  /* ── Summary card ── */
  summaryCard: {
    padding: 20,
    gap: 14,
    ...surfaceCard,
  },
  summaryHead: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
  },
  summaryTitleRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
  },
  summaryDot: {
    width: 8,
    height: 8,
    borderRadius: 4,
    backgroundColor: colors.primary,
  },
  summaryTitle: {
    fontSize: 17,
    fontWeight: "700",
    color: colors.text,
  },
  summaryBadgeWrap: {
    flexDirection: "row",
    alignItems: "center",
    gap: 5,
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderRadius: radii.full,
    backgroundColor: colors.accentLight,
  },
  summaryBadgeDot: {
    width: 6,
    height: 6,
    borderRadius: 3,
    backgroundColor: colors.accent,
  },
  summaryBadge: {
    fontSize: 12,
    fontWeight: "700",
    color: "#166534",
  },
  summaryDivider: {
    height: StyleSheet.hairlineWidth,
    backgroundColor: colors.borderLight,
  },
  summaryRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
  },
  summaryLabel: {
    fontSize: 14,
    color: colors.text3,
  },
  summaryValue: {
    flex: 1,
    textAlign: "right",
    fontSize: 14,
    fontWeight: "700",
    color: colors.text2,
  },
  summaryValueHighlight: {
    color: colors.primary,
  },

  /* ── Menu ── */
  menuCard: {
    paddingHorizontal: 18,
    ...surfaceCard,
  },
  menuRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 14,
    paddingVertical: 18,
  },
  menuIconWrap: {
    width: 44,
    height: 44,
    borderRadius: 22,
    alignItems: "center",
    justifyContent: "center",
  },
  menuTexts: {
    flex: 1,
    gap: 3,
  },
  menuLabel: {
    fontSize: 16,
    fontWeight: "700",
    color: colors.text,
  },
  menuDesc: {
    fontSize: 12,
    color: colors.text3,
  },
  menuArrow: {
    width: 28,
    height: 28,
    borderRadius: 14,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: colors.bg,
  },
  divider: {
    height: StyleSheet.hairlineWidth,
    marginLeft: 58,
    backgroundColor: colors.borderLight,
  },

  /* ── Logout ── */
  logoutBtn: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 8,
    height: 54,
    borderRadius: radii.lg,
    backgroundColor: colors.surface,
    borderWidth: 1.5,
    borderColor: colors.dangerLight,
    ...shadowSm,
  },
  logoutText: {
    fontSize: 15,
    fontWeight: "700",
    color: colors.danger,
  },
});
