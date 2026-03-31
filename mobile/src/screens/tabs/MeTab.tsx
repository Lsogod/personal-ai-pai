import { Alert, Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Ionicons } from "@expo/vector-icons";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { fetchConversations, fetchProfile, fetchSkills, getSourcePlatformLabel } from "../../lib/api";
import { useAuthStore } from "../../store/auth";
import { colors, radii, spacing, surfaceCard } from "../../design/tokens";
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
  { key: "skills", title: "技能管理", desc: "查看和管理 AI 技能", icon: "sparkles-outline" as const, color: colors.iconBgPurple },
  { key: "binding", title: "账号绑定", desc: "多平台数据同步", icon: "link-outline" as const, color: colors.iconBgPrimary },
  { key: "notifications", title: "推送通知", desc: "日程提醒推送设置", icon: "notifications-outline" as const, color: colors.iconBgOrange },
  { key: "feedback", title: "问题反馈", desc: "帮助我们改进", icon: "chatbubble-ellipses-outline" as const, color: colors.iconBgGreen },
] as const;

export function MeTab({ bottomInset, onNavigate, onLogout }: MeTabProps) {
  const token = useAuthStore((state) => state.token);
  const insets = useSafeAreaInsets();
  const [panel, setPanel] = useState<"skills" | "binding" | "feedback" | null>(null);

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
        <View style={[styles.inner, { paddingTop: insets.top + 8 }]}>
          <View style={styles.profileCard}>
            <View style={styles.heroGlowOne} />
            <View style={styles.heroGlowTwo} />
            <View style={styles.profileTop}>
              <View style={styles.avatarRing}>
                <Ionicons name="person" size={26} color="#fff" />
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
              <View style={styles.progressTrack}>
                <View style={[styles.progressFill, { width: progressWidth }]} />
              </View>
              <Text style={styles.progressText}>账号完成度 {setupProgress}/3</Text>
            </View>

            <View style={styles.profileStats}>
              <View style={styles.profileStat}>
                <Text style={styles.profileStatValue}>{activeSkillCount}</Text>
                <Text style={styles.profileStatLabel}>启用技能</Text>
              </View>
              <View style={styles.profileStat}>
                <Text style={styles.profileStatValue}>{conversationCount}</Text>
                <Text style={styles.profileStatLabel}>对话数</Text>
              </View>
              <View style={styles.profileStat}>
                <Text style={styles.profileStatValue}>{platformLabel || "云端"}</Text>
                <Text style={styles.profileStatLabel}>当前端</Text>
              </View>
            </View>
          </View>

          <View style={styles.quickPanel}>
            <Pressable style={styles.quickCard} onPress={() => onNavigate("chat")}>
              <Ionicons name="chatbubble-ellipses-outline" size={22} color={colors.primary} />
              <Text style={styles.quickTitle}>继续对话</Text>
              <Text style={styles.quickDesc}>直接打开助手</Text>
            </Pressable>
            <Pressable style={styles.quickCard} onPress={() => onNavigate("ledger")}>
              <Ionicons name="wallet-outline" size={22} color={colors.accent} />
              <Text style={styles.quickTitle}>查看账单</Text>
              <Text style={styles.quickDesc}>月度支出与明细</Text>
            </Pressable>
            <Pressable style={styles.quickCard} onPress={() => onNavigate("calendar")}>
              <Ionicons name="calendar-outline" size={22} color={colors.warning} />
              <Text style={styles.quickTitle}>提醒安排</Text>
              <Text style={styles.quickDesc}>管理待办与日程</Text>
            </Pressable>
          </View>

          <View style={styles.summaryCard}>
            <View style={styles.summaryHead}>
              <Text style={styles.summaryTitle}>账号状态</Text>
              <Text style={styles.summaryBadge}>已同步</Text>
            </View>
            <View style={styles.summaryRow}>
              <Text style={styles.summaryLabel}>主账号</Text>
              <Text style={styles.summaryValue}>{profile?.email || "未绑定邮箱"}</Text>
            </View>
            <View style={styles.summaryRow}>
              <Text style={styles.summaryLabel}>数据来源</Text>
              <Text style={styles.summaryValue}>{platformLabel || "Web"}</Text>
            </View>
            <View style={styles.summaryRow}>
              <Text style={styles.summaryLabel}>建议下一步</Text>
              <Text style={styles.summaryValue}>{activeSkillCount > 0 ? "补推送设置" : "先启用技能"}</Text>
            </View>
          </View>

          <View style={styles.menuCard}>
            {MENU_ITEMS.map((item, index) => (
              <View key={item.key}>
                <Pressable style={styles.menuRow} onPress={() => handleMenuPress(item)}>
                  <View style={[styles.menuIconWrap, { backgroundColor: item.color }]}>
                    <Ionicons name={item.icon} size={20} color={colors.text} />
                  </View>
                  <View style={styles.menuTexts}>
                    <Text style={styles.menuLabel}>{item.title}</Text>
                    <Text style={styles.menuDesc}>{item.desc}</Text>
                  </View>
                  <Ionicons name="chevron-forward" size={18} color={colors.text4} />
                </Pressable>
                {index < MENU_ITEMS.length - 1 ? <View style={styles.divider} /> : null}
              </View>
            ))}
          </View>

          <Pressable style={styles.logoutBtn} onPress={() => void onLogout()}>
            <Ionicons name="log-out-outline" size={18} color={colors.text3} />
            <Text style={styles.logoutText}>退出登录</Text>
          </Pressable>
        </View>
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
  profileCard: {
    overflow: "hidden",
    padding: 22,
    borderRadius: radii.xl,
    backgroundColor: colors.primary,
    gap: 18,
  },
  heroGlowOne: {
    position: "absolute",
    top: -28,
    right: -18,
    width: 128,
    height: 128,
    borderRadius: 999,
    backgroundColor: "rgba(255,255,255,0.16)",
  },
  heroGlowTwo: {
    position: "absolute",
    bottom: -42,
    left: -20,
    width: 132,
    height: 132,
    borderRadius: 999,
    backgroundColor: "rgba(255,255,255,0.1)",
  },
  profileTop: {
    flexDirection: "row",
    alignItems: "center",
    gap: 16,
  },
  avatarRing: {
    width: 60,
    height: 60,
    borderRadius: 30,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: "rgba(255,255,255,0.2)",
    borderWidth: 2,
    borderColor: "rgba(255,255,255,0.32)",
  },
  profileInfo: {
    flex: 1,
    gap: 4,
  },
  profileName: {
    fontSize: 22,
    fontWeight: "700",
    color: "#fff",
  },
  profileMeta: {
    fontSize: 14,
    color: "rgba(255,255,255,0.76)",
  },
  profileAction: {
    width: 38,
    height: 38,
    borderRadius: 19,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: "rgba(255,255,255,0.15)",
  },
  progressWrap: {
    gap: 8,
  },
  progressTrack: {
    height: 8,
    borderRadius: 999,
    backgroundColor: "rgba(255,255,255,0.18)",
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
    color: "rgba(255,255,255,0.82)",
  },
  profileStats: {
    flexDirection: "row",
    gap: 10,
  },
  profileStat: {
    flex: 1,
    paddingVertical: 12,
    borderRadius: radii.md,
    alignItems: "center",
    gap: 4,
    backgroundColor: "rgba(255,255,255,0.12)",
  },
  profileStatValue: {
    fontSize: 18,
    fontWeight: "800",
    color: "#fff",
  },
  profileStatLabel: {
    fontSize: 12,
    color: "rgba(255,255,255,0.72)",
  },
  quickPanel: {
    flexDirection: "row",
    gap: 10,
  },
  quickCard: {
    flex: 1,
    paddingVertical: 18,
    paddingHorizontal: 12,
    gap: 10,
    ...surfaceCard,
  },
  quickTitle: {
    fontSize: 15,
    fontWeight: "700",
    color: colors.text,
  },
  quickDesc: {
    fontSize: 12,
    lineHeight: 17,
    color: colors.text3,
  },
  summaryCard: {
    padding: 18,
    gap: 12,
    ...surfaceCard,
  },
  summaryHead: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
  },
  summaryTitle: {
    fontSize: 17,
    fontWeight: "700",
    color: colors.text,
  },
  summaryBadge: {
    paddingHorizontal: 10,
    paddingVertical: 5,
    borderRadius: radii.full,
    backgroundColor: colors.accentLight,
    fontSize: 11,
    fontWeight: "700",
    color: "#166534",
  },
  summaryRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: 12,
  },
  summaryLabel: {
    fontSize: 13,
    color: colors.text3,
  },
  summaryValue: {
    flex: 1,
    textAlign: "right",
    fontSize: 13,
    fontWeight: "700",
    color: colors.text2,
  },
  menuCard: {
    paddingHorizontal: 16,
    ...surfaceCard,
  },
  menuRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 14,
    paddingVertical: 16,
  },
  menuIconWrap: {
    width: 42,
    height: 42,
    borderRadius: 21,
    alignItems: "center",
    justifyContent: "center",
  },
  menuTexts: {
    flex: 1,
    gap: 2,
  },
  menuLabel: {
    fontSize: 16,
    fontWeight: "600",
    color: colors.text,
  },
  menuDesc: {
    fontSize: 12,
    color: colors.text3,
  },
  divider: {
    height: StyleSheet.hairlineWidth,
    marginLeft: 56,
    backgroundColor: colors.borderLight,
  },
  logoutBtn: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 8,
    height: 52,
    borderRadius: radii.lg,
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.borderLight,
  },
  logoutText: {
    fontSize: 15,
    fontWeight: "600",
    color: colors.text3,
  },
});
