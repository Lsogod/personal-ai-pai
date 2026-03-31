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

  const profile = profileQuery.data;

  function handleMenuPress(item: (typeof MENU_ITEMS)[number]) {
    if (item.key === "skills" || item.key === "binding" || item.key === "feedback") {
      setPanel(item.key);
      return;
    }
    Alert.alert("推送通知", "原生推送功能即将上线，敬请期待。");
  }

  return (
    <>
      <ScrollView
        style={styles.page}
        contentContainerStyle={{ paddingBottom: bottomInset + 20 }}
        showsVerticalScrollIndicator={false}
      >
        <View style={[styles.inner, { paddingTop: insets.top + 8 }]}>
          {/* ---- Profile header ---- */}
          <View style={styles.profileCard}>
            <View style={styles.avatarRing}>
              <Ionicons name="person" size={26} color="#fff" />
            </View>
            <View style={styles.profileInfo}>
              <Text style={styles.profileName}>{profile?.nickname || "用户"}</Text>
              <Text style={styles.profileMeta}>
                {profile?.email || profile?.platform || "PAI 用户"}
              </Text>
            </View>
          </View>

          {/* ---- Menu ---- */}
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

          {/* ---- Quick links ---- */}
          <View style={styles.linkRow}>
            <Pressable style={styles.linkCard} onPress={() => onNavigate("chat")}>
              <Ionicons name="chatbubble-outline" size={20} color={colors.primary} />
              <Text style={styles.linkText}>对话</Text>
            </Pressable>
            <Pressable style={styles.linkCard} onPress={() => onNavigate("ledger")}>
              <Ionicons name="wallet-outline" size={20} color={colors.accent} />
              <Text style={styles.linkText}>账单</Text>
            </Pressable>
            <Pressable style={styles.linkCard} onPress={() => onNavigate("calendar")}>
              <Ionicons name="calendar-outline" size={20} color={colors.warning} />
              <Text style={styles.linkText}>日程</Text>
            </Pressable>
          </View>

          {/* ---- Logout ---- */}
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

  /* Profile */
  profileCard: {
    flexDirection: "row",
    alignItems: "center",
    gap: 16,
    padding: 22,
    borderRadius: radii.xl,
    backgroundColor: colors.primary,
  },
  avatarRing: {
    width: 60,
    height: 60,
    borderRadius: 30,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: "rgba(255,255,255,0.2)",
    borderWidth: 2,
    borderColor: "rgba(255,255,255,0.3)",
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
    color: "rgba(255,255,255,0.75)",
  },

  /* Menu */
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

  /* Quick links */
  linkRow: {
    flexDirection: "row",
    gap: 10,
  },
  linkCard: {
    flex: 1,
    alignItems: "center",
    gap: 8,
    paddingVertical: 18,
    ...surfaceCard,
  },
  linkText: {
    fontSize: 13,
    fontWeight: "600",
    color: colors.text2,
  },

  /* Logout */
  logoutBtn: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 8,
    height: 50,
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
