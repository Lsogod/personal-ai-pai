import { Ionicons } from "@expo/vector-icons";
import { ScrollView, StyleSheet, Text, Pressable, View } from "react-native";
import { useQuery } from "@tanstack/react-query";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { fetchLedgerStats, fetchProfile } from "../../lib/api";
import { useAuthStore } from "../../store/auth";
import { colors, radii, shadowSm, spacing, surfaceCard } from "../../design/tokens";
import type { TabKey } from "../../components/MiniTabBar";

type HomeTabProps = {
  bottomInset: number;
  onNavigate: (tab: TabKey) => void;
};

const QUICK_ENTRIES = [
  { key: "ledger", label: "记一笔", desc: "跳到账单概览", icon: "receipt-outline", bg: colors.iconBgGreen },
  { key: "calendar", label: "新增日程", desc: "查看本月安排", icon: "calendar-outline", bg: colors.iconBgPrimary },
  { key: "me", label: "我的", desc: "账号与设置", icon: "person-outline", bg: colors.iconBgPurple },
] as const;

const CAPABILITIES = [
  { title: "账单管理", desc: "支持分类、明细、月度概览和最近记录查看。", icon: "wallet-outline", bg: colors.iconBgPrimary },
  { title: "日程提醒", desc: "月视图查看提醒状态，按天聚合账单与日程。", icon: "time-outline", bg: colors.iconBgPink },
  { title: "多端同步", desc: "保留统一后端，移动端与 Web/小程序共用同一份数据。", icon: "sync-outline", bg: colors.iconBgGreen },
  { title: "个人中心", desc: "账户信息、绑定入口和后续原生推送设置统一收口。", icon: "construct-outline", bg: colors.iconBgOrange },
] as const;

export function HomeTab({ bottomInset, onNavigate }: HomeTabProps) {
  const token = useAuthStore((state) => state.token);
  const insets = useSafeAreaInsets();

  const profileQuery = useQuery({
    queryKey: ["profile"],
    enabled: !!token,
    queryFn: () => fetchProfile(token!),
  });

  const statsQuery = useQuery({
    queryKey: ["stats", "month"],
    enabled: !!token,
    queryFn: () => fetchLedgerStats(token!, "month"),
  });

  const nickname = profileQuery.data?.nickname || "你";
  const total = Number(statsQuery.data?.total || 0).toFixed(0);
  const count = Number(statsQuery.data?.count || 0);

  return (
    <ScrollView
      style={styles.page}
      contentContainerStyle={[
        styles.content,
        { paddingTop: insets.top + 6, paddingBottom: bottomInset + 24 },
      ]}
      showsVerticalScrollIndicator={false}
    >
      <View style={styles.hero}>
        <View style={styles.heroGlow} />
        <View style={styles.heroBadge}>
          <View style={styles.heroBadgeDot} />
          <Text style={styles.heroBadgeText}>PAI</Text>
        </View>
        <Text style={styles.heroTitle}>你的个人效率工具</Text>
        <Text style={styles.heroDesc}>
          {nickname}，现在这版移动端按小程序的信息结构重做，登录仍然沿用邮箱体系，账单与日程继续走统一后端。
        </Text>
        <View style={styles.heroMetaRow}>
          <View style={styles.heroMetaPill}>
            <Text style={styles.heroMetaLabel}>本月支出</Text>
            <Text style={styles.heroMetaValue}>¥{total}</Text>
          </View>
          <View style={styles.heroMetaPill}>
            <Text style={styles.heroMetaLabel}>记录数</Text>
            <Text style={styles.heroMetaValue}>{count} 笔</Text>
          </View>
        </View>
        <View style={styles.heroActions}>
          <Pressable style={styles.heroPrimaryBtn} onPress={() => onNavigate("command")}>
            <Text style={styles.heroPrimaryText}>打开指令面板</Text>
          </Pressable>
          <Pressable style={styles.heroSecondaryBtn} onPress={() => onNavigate("me")}>
            <Text style={styles.heroSecondaryText}>个人中心</Text>
          </Pressable>
        </View>
      </View>

      <View style={styles.section}>
        <View style={styles.sectionHead}>
          <Text style={styles.sectionTitle}>常用入口</Text>
        </View>
        <View style={styles.entryGrid}>
          {QUICK_ENTRIES.map((item) => (
            <Pressable key={item.label} style={styles.entryCard} onPress={() => onNavigate(item.key as TabKey)}>
              <View style={[styles.entryIconWrap, { backgroundColor: item.bg }]}>
                <Ionicons name={item.icon} size={22} color={colors.text} />
              </View>
              <Text style={styles.entryName}>{item.label}</Text>
              <Text style={styles.entryDesc}>{item.desc}</Text>
            </Pressable>
          ))}
        </View>
      </View>

      <View style={styles.section}>
        <View style={styles.sectionHead}>
          <Text style={styles.sectionTitle}>核心能力</Text>
          <Pressable onPress={() => onNavigate("command")}>
            <Text style={styles.sectionMore}>查看指令</Text>
          </Pressable>
        </View>
        <View style={styles.capGrid}>
          {CAPABILITIES.map((item) => (
            <View key={item.title} style={styles.capCard}>
              <View style={[styles.capIcon, { backgroundColor: item.bg }]}>
                <Ionicons name={item.icon} size={22} color={colors.text} />
              </View>
              <Text style={styles.capName}>{item.title}</Text>
              <Text style={styles.capDesc}>{item.desc}</Text>
            </View>
          ))}
        </View>
      </View>

      <Text style={styles.footer}>PAI · React Native 原生版（小程序 UI/UX 对齐中）</Text>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  page: {
    flex: 1,
    backgroundColor: colors.bg,
  },
  content: {
    paddingHorizontal: spacing.pageX,
    gap: 20,
  },
  hero: {
    padding: 24,
    backgroundColor: colors.primary,
    borderRadius: radii.xl,
    overflow: "hidden",
  },
  heroGlow: {
    position: "absolute",
    right: -36,
    top: -28,
    width: 170,
    height: 170,
    borderRadius: 999,
    backgroundColor: "rgba(255,255,255,0.1)",
  },
  heroBadge: {
    alignSelf: "flex-start",
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    paddingHorizontal: 14,
    paddingVertical: 6,
    borderRadius: radii.full,
    backgroundColor: "rgba(255,255,255,0.18)",
    marginBottom: 14,
  },
  heroBadgeDot: {
    width: 8,
    height: 8,
    borderRadius: 999,
    backgroundColor: "#6ee7b7",
  },
  heroBadgeText: {
    fontSize: 12,
    fontWeight: "700",
    letterSpacing: 1.2,
    color: "#ffffff",
  },
  heroTitle: {
    fontSize: 28,
    fontWeight: "800",
    color: "#ffffff",
    marginBottom: 8,
  },
  heroDesc: {
    fontSize: 14,
    lineHeight: 21,
    color: "rgba(255,255,255,0.92)",
    marginBottom: 18,
  },
  heroMetaRow: {
    flexDirection: "row",
    gap: 10,
    marginBottom: 18,
  },
  heroMetaPill: {
    flex: 1,
    padding: 12,
    borderRadius: radii.lg,
    backgroundColor: "rgba(255,255,255,0.12)",
  },
  heroMetaLabel: {
    fontSize: 12,
    color: "rgba(255,255,255,0.75)",
    marginBottom: 4,
  },
  heroMetaValue: {
    fontSize: 19,
    fontWeight: "800",
    color: "#ffffff",
  },
  heroActions: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 10,
  },
  heroPrimaryBtn: {
    flex: 1,
    minWidth: 140,
    alignItems: "center",
    justifyContent: "center",
    borderRadius: radii.md,
    backgroundColor: "#ffffff",
    paddingVertical: 14,
    ...shadowSm,
  },
  heroPrimaryText: {
    fontSize: 14,
    fontWeight: "700",
    color: colors.primary,
  },
  heroSecondaryBtn: {
    flex: 1,
    minWidth: 140,
    alignItems: "center",
    justifyContent: "center",
    borderRadius: radii.md,
    backgroundColor: "rgba(255,255,255,0.18)",
    borderWidth: 1,
    borderColor: "rgba(255,255,255,0.24)",
    paddingVertical: 14,
  },
  heroSecondaryText: {
    fontSize: 14,
    fontWeight: "700",
    color: "#ffffff",
  },
  section: {
    gap: 12,
  },
  sectionHead: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingHorizontal: 2,
  },
  sectionTitle: {
    fontSize: 20,
    fontWeight: "700",
    color: colors.text,
  },
  sectionMore: {
    fontSize: 12,
    fontWeight: "700",
    color: colors.primary,
    backgroundColor: colors.primaryLight,
    borderRadius: radii.full,
    overflow: "hidden",
    paddingHorizontal: 10,
    paddingVertical: 5,
  },
  entryGrid: {
    flexDirection: "row",
    gap: 10,
  },
  entryCard: {
    flex: 1,
    alignItems: "center",
    gap: 7,
    paddingHorizontal: 10,
    paddingVertical: 18,
    ...surfaceCard,
  },
  entryIconWrap: {
    width: 42,
    height: 42,
    borderRadius: radii.md,
    alignItems: "center",
    justifyContent: "center",
  },
  entryName: {
    fontSize: 14,
    fontWeight: "700",
    color: colors.text2,
  },
  entryDesc: {
    fontSize: 11,
    lineHeight: 16,
    textAlign: "center",
    color: colors.text4,
  },
  capGrid: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 12,
  },
  capCard: {
    width: "48%",
    padding: 18,
    gap: 10,
    ...surfaceCard,
  },
  capIcon: {
    width: 46,
    height: 46,
    borderRadius: radii.md,
    alignItems: "center",
    justifyContent: "center",
  },
  capName: {
    fontSize: 16,
    fontWeight: "700",
    color: colors.text,
  },
  capDesc: {
    fontSize: 12,
    lineHeight: 18,
    color: colors.text3,
  },
  footer: {
    textAlign: "center",
    color: colors.text4,
    fontSize: 12,
    paddingTop: 4,
  },
});
