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

function getGreeting(): string {
  const h = new Date().getHours();
  if (h < 6) return "夜深了";
  if (h < 11) return "早上好";
  if (h < 14) return "中午好";
  if (h < 18) return "下午好";
  return "晚上好";
}

const QUICK_ACTIONS = [
  { key: "ledger", icon: "wallet-outline" as const, label: "账单", desc: "收支和明细", bg: colors.accentLight, color: colors.accent },
  { key: "calendar", icon: "calendar-outline" as const, label: "日程", desc: "提醒和安排", bg: colors.warningLight, color: colors.warning },
  { key: "chat", icon: "sparkles-outline" as const, label: "助手", desc: "记账提醒都能说", bg: colors.primaryLight, color: colors.primary },
  { key: "me", icon: "person-outline" as const, label: "我的", desc: "账号设置", bg: colors.iconBgPurple, color: "#9333ea" },
] as const;

export function StatsTab({ bottomInset, onNavigate }: HomeTabProps) {
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
  const aiEmoji = profileQuery.data?.ai_emoji || "✨";
  const total = Number(statsQuery.data?.total || 0).toFixed(0);
  const count = Number(statsQuery.data?.count || 0);

  return (
    <ScrollView
      style={styles.page}
      contentContainerStyle={[
        styles.content,
        { paddingTop: insets.top + 12, paddingBottom: bottomInset + 20 },
      ]}
      showsVerticalScrollIndicator={false}
    >
      <View style={styles.heroCard}>
        <View style={styles.heroTop}>
          <View>
            <Text style={styles.heroEyebrow}>{getGreeting()} · {nickname}</Text>
            <Text style={styles.heroTitle}>统计总览</Text>
          </View>
          <Pressable style={styles.heroAvatarBtn} onPress={() => onNavigate("me")}>
            <Text style={styles.heroAvatarEmoji}>{aiEmoji}</Text>
          </Pressable>
        </View>
        <Text style={styles.heroDesc}>这里不再放首页，直接把月度统计、快捷入口和助手工作台合到一个统计页。</Text>
        <View style={styles.heroMetrics}>
          <View style={styles.heroMetric}>
            <Text style={styles.heroMetricValue}>¥{total}</Text>
            <Text style={styles.heroMetricLabel}>本月支出</Text>
          </View>
          <View style={styles.heroMetric}>
            <Text style={styles.heroMetricValue}>{count}</Text>
            <Text style={styles.heroMetricLabel}>记录笔数</Text>
          </View>
          <View style={styles.heroMetric}>
            <Text style={styles.heroMetricValue}>
              {count > 0 ? `¥${(Number(total) / new Date().getDate()).toFixed(0)}` : "—"}
            </Text>
            <Text style={styles.heroMetricLabel}>日均</Text>
          </View>
        </View>
      </View>

      <View style={styles.section}>
        <Text style={styles.sectionTitle}>工作台</Text>
        <View style={styles.actionGrid}>
          {QUICK_ACTIONS.map((item) => (
            <Pressable key={item.key} style={styles.actionCard} onPress={() => onNavigate(item.key as TabKey)}>
              <View style={[styles.actionIcon, { backgroundColor: item.bg }]}>
                <Ionicons name={item.icon} size={22} color={item.color} />
              </View>
              <Text style={styles.actionLabel}>{item.label}</Text>
              <Text style={styles.actionDesc}>{item.desc}</Text>
            </Pressable>
          ))}
        </View>
      </View>

      <View style={styles.section}>
        <Text style={styles.sectionTitle}>助手入口</Text>
        <Pressable style={styles.aiCard} onPress={() => onNavigate("chat")}>
          <View style={styles.aiCardInner}>
            <View style={styles.aiAvatarLarge}>
              <Text style={{ fontSize: 28 }}>{aiEmoji}</Text>
            </View>
            <View style={styles.aiCardContent}>
              <Text style={styles.aiCardTitle}>打开 PAI 助手</Text>
              <Text style={styles.aiCardDesc}>直接说自然语言，记账、提醒、查询和创作都在这里完成。</Text>
            </View>
            <View style={styles.aiCardArrow}>
              <Ionicons name="arrow-forward" size={18} color="rgba(255,255,255,0.8)" />
            </View>
          </View>
        </Pressable>
      </View>

      <View style={styles.section}>
        <Text style={styles.sectionTitle}>核心指标</Text>
        <View style={styles.statsRow}>
          <Pressable style={styles.statCard} onPress={() => onNavigate("ledger")}>
            <Ionicons name="trending-up" size={20} color={colors.accent} />
            <Text style={styles.statValue}>¥{total}</Text>
            <Text style={styles.statLabel}>总支出</Text>
          </Pressable>
          <Pressable style={styles.statCard} onPress={() => onNavigate("ledger")}>
            <Ionicons name="receipt-outline" size={20} color={colors.primary} />
            <Text style={styles.statValue}>{count}</Text>
            <Text style={styles.statLabel}>记录数</Text>
          </Pressable>
          <Pressable style={styles.statCard} onPress={() => onNavigate("calendar")}>
            <Ionicons name="checkmark-circle-outline" size={20} color={colors.warning} />
            <Text style={styles.statValue}>
              {count > 0 ? `¥${(Number(total) / new Date().getDate()).toFixed(0)}` : "—"}
            </Text>
            <Text style={styles.statLabel}>日均</Text>
          </Pressable>
        </View>
      </View>
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
    gap: 22,
  },
  heroCard: {
    gap: 14,
    padding: 22,
    borderRadius: radii.xl,
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.borderLight,
    ...shadowSm,
  },
  heroTop: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
  },
  heroEyebrow: {
    fontSize: 13,
    fontWeight: "700",
    color: colors.text3,
  },
  heroTitle: {
    marginTop: 4,
    fontSize: 28,
    fontWeight: "800",
    color: colors.text,
  },
  heroAvatarBtn: {
    width: 52,
    height: 52,
    borderRadius: 26,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: colors.primaryLight,
  },
  heroAvatarEmoji: {
    fontSize: 24,
  },
  heroDesc: {
    fontSize: 14,
    lineHeight: 21,
    color: colors.text3,
  },
  heroMetrics: {
    flexDirection: "row",
    gap: 10,
  },
  heroMetric: {
    flex: 1,
    gap: 4,
    padding: 14,
    borderRadius: radii.md,
    backgroundColor: colors.bg,
  },
  heroMetricValue: {
    fontSize: 18,
    fontWeight: "800",
    color: colors.text,
  },
  heroMetricLabel: {
    fontSize: 12,
    color: colors.text3,
  },
  aiCard: {
    borderRadius: radii.xl,
    backgroundColor: colors.primary,
    overflow: "hidden",
  },
  aiCardInner: {
    flexDirection: "row",
    alignItems: "center",
    padding: 20,
    gap: 14,
  },
  aiAvatarLarge: {
    width: 56,
    height: 56,
    borderRadius: 28,
    backgroundColor: "rgba(255,255,255,0.2)",
    alignItems: "center",
    justifyContent: "center",
  },
  aiCardContent: {
    flex: 1,
  },
  aiCardTitle: {
    fontSize: 18,
    fontWeight: "700",
    color: "#fff",
  },
  aiCardDesc: {
    fontSize: 13,
    color: "rgba(255,255,255,0.8)",
    marginTop: 4,
  },
  aiCardArrow: {
    width: 36,
    height: 36,
    borderRadius: 18,
    backgroundColor: "rgba(255,255,255,0.15)",
    alignItems: "center",
    justifyContent: "center",
  },

  /* Section */
  section: {
    gap: 12,
  },
  sectionTitle: {
    fontSize: 18,
    fontWeight: "700",
    color: colors.text,
    paddingHorizontal: 2,
  },

  /* Quick actions */
  actionGrid: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 10,
  },
  actionCard: {
    width: "48%",
    flexGrow: 1,
    alignItems: "center",
    gap: 8,
    paddingVertical: 20,
    paddingHorizontal: 10,
    ...surfaceCard,
  },
  actionIcon: {
    width: 46,
    height: 46,
    borderRadius: 23,
    alignItems: "center",
    justifyContent: "center",
  },
  actionLabel: {
    fontSize: 15,
    fontWeight: "700",
    color: colors.text,
  },
  actionDesc: {
    fontSize: 12,
    color: colors.text3,
  },

  /* Stats */
  statsRow: {
    flexDirection: "row",
    gap: 10,
  },
  statCard: {
    flex: 1,
    alignItems: "center",
    gap: 8,
    paddingVertical: 18,
    ...surfaceCard,
  },
  statValue: {
    fontSize: 20,
    fontWeight: "800",
    color: colors.text,
  },
  statLabel: {
    fontSize: 12,
    color: colors.text3,
  },
});
