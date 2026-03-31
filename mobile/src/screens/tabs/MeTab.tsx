import { Alert, Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Ionicons } from "@expo/vector-icons";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { fetchProfile } from "../../lib/api";
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
  { key: "skills", title: "技能管理", icon: "sparkles-outline", color: colors.iconBgPurple, status: "已接入" },
  { key: "binding", title: "账号绑定", icon: "link-outline", color: colors.iconBgPrimary, status: "已接入" },
  { key: "notifications", title: "提醒订阅", icon: "notifications-outline", color: colors.iconBgOrange, status: "原生推送待接入" },
  { key: "feedback", title: "问题反馈", icon: "chatbubble-ellipses-outline", color: colors.iconBgGreen, status: "已接入" },
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

  const profile = profileQuery.data;

  function handleMenuPress(item: (typeof MENU_ITEMS)[number]) {
    if (item.key === "skills" || item.key === "binding" || item.key === "feedback") {
      setPanel(item.key);
      return;
    }
    Alert.alert("提醒订阅", "App 端会改成 APNs/FCM 原生推送，这一项后面继续接。");
  }

  return (
    <>
      <ScrollView
        style={styles.page}
        contentContainerStyle={{ paddingBottom: bottomInset + 20 }}
        showsVerticalScrollIndicator={false}
      >
        <View style={[styles.inner, { paddingTop: insets.top + 8 }]}>
          <View style={styles.hero}>
            <View style={styles.heroGlow} />
            <View style={styles.avatarRing}>
              <Ionicons name="person-outline" size={28} color="#ffffff" />
            </View>
            <Text style={styles.heroName}>{profile?.nickname || "用户"}</Text>
            <Text style={styles.heroSub}>
              {profile?.platform || "web"} · 阶段 {profile?.setup_stage ?? 0}
            </Text>
            {profile?.email ? <Text style={styles.heroEmail}>{profile.email}</Text> : null}
          </View>

          <View style={styles.card}>
            {MENU_ITEMS.map((item, index) => (
              <View key={item.title}>
                <Pressable style={styles.menuRow} onPress={() => handleMenuPress(item)}>
                  <View style={[styles.menuIconWrap, { backgroundColor: item.color }]}>
                    <Ionicons name={item.icon} size={20} color={colors.text} />
                  </View>
                  <Text style={styles.menuLabel}>{item.title}</Text>
                  <Text style={styles.menuStatus}>{item.status}</Text>
                  <Ionicons name="chevron-forward" size={18} color={colors.text4} />
                </Pressable>
                {index < MENU_ITEMS.length - 1 ? <View style={styles.line} /> : null}
              </View>
            ))}
          </View>

          <View style={styles.card}>
            <Pressable style={styles.quickAction} onPress={() => onNavigate("home")}>
              <Ionicons name="home-outline" size={18} color={colors.primary} />
              <Text style={styles.quickActionText}>返回首页</Text>
            </Pressable>
            <Pressable style={styles.quickAction} onPress={() => onNavigate("command")}>
              <Ionicons name="code-slash-outline" size={18} color={colors.primary} />
              <Text style={styles.quickActionText}>打开指令面板</Text>
            </Pressable>
          </View>

          <Pressable style={styles.logoutBtn} onPress={() => void onLogout()}>
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
  hero: {
    overflow: "hidden",
    alignItems: "center",
    paddingHorizontal: 22,
    paddingVertical: 28,
    borderRadius: radii.xl,
    backgroundColor: colors.primary,
  },
  heroGlow: {
    position: "absolute",
    right: -42,
    top: -38,
    width: 160,
    height: 160,
    borderRadius: 999,
    backgroundColor: "rgba(255,255,255,0.1)",
  },
  avatarRing: {
    width: 74,
    height: 74,
    borderRadius: 999,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: "rgba(255,255,255,0.2)",
    borderWidth: 2,
    borderColor: "rgba(255,255,255,0.34)",
    marginBottom: 12,
  },
  heroName: {
    fontSize: 26,
    fontWeight: "800",
    color: "#ffffff",
  },
  heroSub: {
    marginTop: 6,
    fontSize: 13,
    color: "rgba(255,255,255,0.76)",
  },
  heroEmail: {
    marginTop: 8,
    fontSize: 13,
    color: "rgba(255,255,255,0.9)",
  },
  card: {
    paddingHorizontal: 18,
    ...surfaceCard,
  },
  menuRow: {
    minHeight: 74,
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
  },
  menuIconWrap: {
    width: 42,
    height: 42,
    borderRadius: radii.md,
    alignItems: "center",
    justifyContent: "center",
  },
  menuLabel: {
    flex: 1,
    fontSize: 16,
    fontWeight: "700",
    color: colors.text,
  },
  menuStatus: {
    maxWidth: 110,
    fontSize: 11,
    fontWeight: "700",
    textAlign: "right",
    color: colors.primary,
  },
  line: {
    height: 1,
    marginLeft: 54,
    backgroundColor: colors.borderLight,
  },
  quickAction: {
    minHeight: 56,
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
  },
  quickActionText: {
    fontSize: 15,
    fontWeight: "700",
    color: colors.primary,
  },
  logoutBtn: {
    alignItems: "center",
    justifyContent: "center",
    height: 56,
    borderRadius: radii.lg,
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.border,
  },
  logoutText: {
    fontSize: 15,
    fontWeight: "700",
    color: colors.text2,
  },
});
