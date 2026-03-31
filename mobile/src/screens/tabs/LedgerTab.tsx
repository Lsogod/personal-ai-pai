import { Ionicons } from "@expo/vector-icons";
import { useMemo, useState } from "react";
import {
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  Pressable,
  View,
} from "react-native";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { fetchLedgers, fetchLedgerStats, LedgerItem } from "../../lib/api";
import { formatDateLabel, formatHmLocal, parseServerDate } from "../../lib/date";
import { useAuthStore } from "../../store/auth";
import { colors, radii, shadowSm, spacing, surfaceCard } from "../../design/tokens";

type LedgerTabProps = {
  bottomInset: number;
};

function groupByDay(rows: LedgerItem[]) {
  const groups = new Map<string, LedgerItem[]>();
  rows.forEach((item) => {
    const key = item.transaction_date.slice(0, 10);
    const current = groups.get(key) || [];
    current.push(item);
    groups.set(key, current);
  });
  return Array.from(groups.entries()).map(([date, items]) => ({
    date,
    label: formatDateLabel(date),
    total: items.reduce((sum, row) => sum + Number(row.amount || 0), 0),
    rows: items,
  }));
}

export function LedgerTab({ bottomInset }: LedgerTabProps) {
  const token = useAuthStore((state) => state.token);
  const queryClient = useQueryClient();
  const insets = useSafeAreaInsets();
  const [activeTab, setActiveTab] = useState<"overview" | "list">("overview");

  const statsQuery = useQuery({
    queryKey: ["stats", "month"],
    enabled: !!token,
    queryFn: () => fetchLedgerStats(token!, "month"),
  });

  const ledgersQuery = useQuery({
    queryKey: ["ledgers"],
    enabled: !!token,
    queryFn: () => fetchLedgers(token!, 40),
  });

  const currentMonth = new Date().getMonth();
  const monthRows = useMemo(
    () =>
      (ledgersQuery.data || []).filter((item) => {
        const date = parseServerDate(item.transaction_date);
        return !Number.isNaN(date.getTime()) && date.getMonth() === currentMonth;
      }),
    [currentMonth, ledgersQuery.data]
  );

  const categoryStats = useMemo(() => {
    const stats = new Map<string, number>();
    monthRows.forEach((item) => {
      const key = item.category || "未分类";
      stats.set(key, (stats.get(key) || 0) + Number(item.amount || 0));
    });
    return Array.from(stats.entries())
      .map(([name, value]) => ({ name, value }))
      .sort((a, b) => b.value - a.value)
      .slice(0, 5);
  }, [monthRows]);

  const groupedRows = useMemo(() => groupByDay(ledgersQuery.data || []), [ledgersQuery.data]);
  const avgDaily = useMemo(() => {
    const today = Math.max(1, new Date().getDate());
    return (Number(statsQuery.data?.total || 0) / today).toFixed(0);
  }, [statsQuery.data?.total]);

  async function refreshAll() {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["stats", "month"] }),
      queryClient.invalidateQueries({ queryKey: ["ledgers"] }),
    ]);
  }

  return (
    <ScrollView
      style={styles.page}
      contentContainerStyle={{ paddingBottom: bottomInset + 18 }}
      refreshControl={<RefreshControl refreshing={ledgersQuery.isRefetching} onRefresh={() => void refreshAll()} />}
      showsVerticalScrollIndicator={false}
    >
      <View style={[styles.inner, { paddingTop: insets.top + 8 }]}>
        <View style={styles.tabBar}>
          <Pressable
            style={[styles.tabItem, activeTab === "overview" && styles.tabItemActive]}
            onPress={() => setActiveTab("overview")}
          >
            <Text style={[styles.tabText, activeTab === "overview" && styles.tabTextActive]}>概览</Text>
          </Pressable>
          <Pressable
            style={[styles.tabItem, activeTab === "list" && styles.tabItemActive]}
            onPress={() => setActiveTab("list")}
          >
            <Text style={[styles.tabText, activeTab === "list" && styles.tabTextActive]}>明细</Text>
          </Pressable>
        </View>

        {activeTab === "overview" ? (
          <>
            <View style={styles.summaryCard}>
              <View style={styles.summaryGlow} />
              <Text style={styles.summaryLabel}>本月概览</Text>
              <View style={styles.summaryRow}>
                <View style={styles.metricItem}>
                  <Text style={styles.metricValue}>¥{Number(statsQuery.data?.total || 0).toFixed(0)}</Text>
                  <Text style={styles.metricLabel}>总支出</Text>
                </View>
                <View style={styles.metricDivider} />
                <View style={styles.metricItem}>
                  <Text style={styles.metricValue}>{Number(statsQuery.data?.count || 0)}</Text>
                  <Text style={styles.metricLabel}>笔数</Text>
                </View>
                <View style={styles.metricDivider} />
                <View style={styles.metricItem}>
                  <Text style={styles.metricValue}>¥{avgDaily}</Text>
                  <Text style={styles.metricLabel}>日均</Text>
                </View>
              </View>
            </View>

            <View style={styles.card}>
              <Text style={styles.cardTitle}>分类占比</Text>
              {categoryStats.length === 0 ? (
                <Text style={styles.emptyText}>本月暂无账单数据</Text>
              ) : (
                categoryStats.map((item, index) => (
                  <View key={item.name} style={styles.categoryRow}>
                    <View style={[styles.categoryDot, { backgroundColor: CATEGORY_COLORS[index % CATEGORY_COLORS.length] }]} />
                    <Text style={styles.categoryName}>{item.name}</Text>
                    <Text style={styles.categoryValue}>¥{item.value.toFixed(0)}</Text>
                  </View>
                ))
              )}
            </View>

            <View style={styles.card}>
              <Text style={styles.cardTitle}>最近记录</Text>
              {monthRows.slice(0, 6).map((item) => (
                <View key={item.id} style={styles.ledgerRow}>
                  <View style={styles.ledgerMain}>
                    <Text style={styles.ledgerName}>{item.item || "未命名账单"}</Text>
                    <Text style={styles.ledgerMeta}>
                      {item.category || "未分类"} · {formatHmLocal(item.transaction_date)}
                    </Text>
                  </View>
                  <Text style={styles.ledgerAmount}>¥{Number(item.amount || 0).toFixed(0)}</Text>
                </View>
              ))}
            </View>
          </>
        ) : (
          <View style={styles.card}>
            <View style={styles.listHead}>
              <Text style={styles.cardTitle}>账单明细</Text>
              <Text style={styles.listMeta}>共 {(ledgersQuery.data || []).length} 条</Text>
            </View>
            {groupedRows.map((group) => (
              <View key={group.date} style={styles.groupWrap}>
                <View style={styles.groupHead}>
                  <Text style={styles.groupTitle}>{group.label}</Text>
                  <Text style={styles.groupMeta}>¥{group.total.toFixed(0)}</Text>
                </View>
                {group.rows.map((row) => (
                  <View key={row.id} style={styles.billItem}>
                    <View style={styles.billTop}>
                      <Text style={styles.billName}>{row.item || "未命名"}</Text>
                      <Text style={styles.billAmount}>¥{Number(row.amount || 0).toFixed(0)}</Text>
                    </View>
                    <View style={styles.billBottom}>
                      <Text style={styles.billMeta}>
                        {row.category || "未分类"} · {formatHmLocal(row.transaction_date)}
                      </Text>
                      <Ionicons name="ellipsis-horizontal" size={16} color={colors.text4} />
                    </View>
                  </View>
                ))}
              </View>
            ))}
          </View>
        )}
      </View>
    </ScrollView>
  );
}

const CATEGORY_COLORS = ["#4f6ef7", "#10b981", "#f59e0b", "#ec4899", "#8b5cf6"];

const styles = StyleSheet.create({
  page: {
    flex: 1,
    backgroundColor: colors.bg,
  },
  inner: {
    paddingHorizontal: spacing.pageX,
    gap: 14,
  },
  tabBar: {
    flexDirection: "row",
    padding: 6,
    borderRadius: radii.lg,
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.borderLight,
  },
  tabItem: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
    paddingVertical: 12,
    borderRadius: radii.md,
  },
  tabItemActive: {
    backgroundColor: colors.primary,
    ...shadowSm,
  },
  tabText: {
    fontSize: 14,
    fontWeight: "700",
    color: colors.text3,
  },
  tabTextActive: {
    color: "#ffffff",
  },
  summaryCard: {
    overflow: "hidden",
    borderRadius: radii.xl,
    backgroundColor: colors.primary,
    paddingHorizontal: 20,
    paddingVertical: 18,
  },
  summaryGlow: {
    position: "absolute",
    right: -30,
    bottom: -38,
    width: 150,
    height: 150,
    borderRadius: 999,
    backgroundColor: "rgba(255,255,255,0.09)",
  },
  summaryLabel: {
    fontSize: 13,
    fontWeight: "700",
    color: "rgba(255,255,255,0.84)",
    marginBottom: 12,
  },
  summaryRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
  },
  metricItem: {
    flex: 1,
    alignItems: "center",
    gap: 4,
  },
  metricDivider: {
    width: 1,
    height: 42,
    backgroundColor: "rgba(255,255,255,0.24)",
  },
  metricValue: {
    fontSize: 24,
    fontWeight: "800",
    color: "#ffffff",
  },
  metricLabel: {
    fontSize: 12,
    color: "rgba(255,255,255,0.75)",
  },
  card: {
    padding: 18,
    gap: 12,
    ...surfaceCard,
  },
  cardTitle: {
    fontSize: 17,
    fontWeight: "700",
    color: colors.text,
  },
  emptyText: {
    fontSize: 13,
    color: colors.text3,
  },
  categoryRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
  },
  categoryDot: {
    width: 10,
    height: 10,
    borderRadius: 999,
  },
  categoryName: {
    flex: 1,
    fontSize: 14,
    color: colors.text,
  },
  categoryValue: {
    fontSize: 14,
    fontWeight: "700",
    color: colors.text2,
  },
  ledgerRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
    paddingVertical: 6,
  },
  ledgerMain: {
    flex: 1,
  },
  ledgerName: {
    fontSize: 15,
    fontWeight: "700",
    color: colors.text,
  },
  ledgerMeta: {
    marginTop: 4,
    fontSize: 12,
    color: colors.text3,
  },
  ledgerAmount: {
    fontSize: 15,
    fontWeight: "800",
    color: colors.primary,
  },
  listHead: {
    flexDirection: "row",
    alignItems: "baseline",
    justifyContent: "space-between",
  },
  listMeta: {
    fontSize: 12,
    color: colors.text3,
  },
  groupWrap: {
    gap: 8,
    paddingTop: 6,
  },
  groupHead: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
  },
  groupTitle: {
    fontSize: 15,
    fontWeight: "700",
    color: colors.text,
  },
  groupMeta: {
    fontSize: 12,
    color: colors.text3,
  },
  billItem: {
    padding: 14,
    borderRadius: radii.md,
    backgroundColor: colors.bg,
  },
  billTop: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
  },
  billName: {
    flex: 1,
    fontSize: 15,
    fontWeight: "700",
    color: colors.text,
  },
  billAmount: {
    fontSize: 15,
    fontWeight: "800",
    color: colors.primary,
  },
  billBottom: {
    marginTop: 8,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
  },
  billMeta: {
    fontSize: 12,
    color: colors.text3,
  },
});
