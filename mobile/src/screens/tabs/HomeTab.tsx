import { Ionicons } from "@expo/vector-icons";
import { type ReactNode, useEffect, useMemo, useRef, useState } from "react";
import {
  Animated,
  NativeScrollEvent,
  NativeSyntheticEvent,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  useWindowDimensions,
  View,
} from "react-native";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import {
  fetchCalendar,
  fetchLedgers,
  fetchProfile,
  fetchSchedules,
  getLedgerDisplayCategory,
  getLedgerEntryKind,
} from "../../lib/api";
import { formatHmLocal, formatMonthLabel, getMonthRange, parseServerDate } from "../../lib/date";
import { colors, radii, shadowSm, spacing, surfaceCard } from "../../design/tokens";
import { useAuthStore } from "../../store/auth";
import type { TabKey } from "../../components/MiniTabBar";

type HomeTabProps = {
  bottomInset: number;
  onNavigate: (tab: TabKey) => void;
};

type TrendPoint = {
  label: string;
  value: number;
};

function StaggerGroup({
  active,
  index,
  children,
}: {
  active: boolean;
  index: number;
  children: ReactNode;
}) {
  const opacity = useRef(new Animated.Value(active ? 1 : 0)).current;
  const translateY = useRef(new Animated.Value(active ? 0 : 18)).current;

  useEffect(() => {
    if (active) {
      Animated.parallel([
        Animated.timing(opacity, {
          toValue: 1,
          duration: 240,
          delay: index * 70,
          useNativeDriver: true,
        }),
        Animated.timing(translateY, {
          toValue: 0,
          duration: 280,
          delay: index * 70,
          useNativeDriver: true,
        }),
      ]).start();
      return;
    }

    opacity.setValue(0);
    translateY.setValue(18);
  }, [active, index, opacity, translateY]);

  return <Animated.View style={{ opacity, transform: [{ translateY }] }}>{children}</Animated.View>;
}

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
  const queryClient = useQueryClient();
  const insets = useSafeAreaInsets();
  const { width: pageWidth } = useWindowDimensions();
  const [activeView, setActiveView] = useState<"overview" | "category" | "trend">("overview");
  const pagerRef = useRef<ScrollView | null>(null);
  const scrollX = useRef(new Animated.Value(0)).current;
  const profileQuery = useQuery({
    queryKey: ["profile"],
    enabled: !!token,
    queryFn: () => fetchProfile(token!),
  });

  const ledgersQuery = useQuery({
    queryKey: ["ledgers", "stats"],
    enabled: !!token,
    queryFn: () => fetchLedgers(token!, 80),
  });

  const schedulesQuery = useQuery({
    queryKey: ["schedules", "stats"],
    enabled: !!token,
    queryFn: () => fetchSchedules(token!, 80),
  });

  const visibleMonth = useMemo(() => {
    const today = new Date();
    const currentMonth = new Date(today.getFullYear(), today.getMonth(), 1);
    const activityDates = [
      ...(ledgersQuery.data || []).map((item) => parseServerDate(item.transaction_date)),
      ...(schedulesQuery.data || []).map((item) => parseServerDate(item.trigger_time)),
    ].filter((item) => !Number.isNaN(item.getTime()));

    if (activityDates.length === 0) return currentMonth;

    const hasCurrentMonth = activityDates.some(
      (item) => item.getFullYear() === currentMonth.getFullYear() && item.getMonth() === currentMonth.getMonth()
    );
    if (hasCurrentMonth) return currentMonth;

    const latest = activityDates.sort((a, b) => b.getTime() - a.getTime())[0];
    return new Date(latest.getFullYear(), latest.getMonth(), 1);
  }, [ledgersQuery.data, schedulesQuery.data]);

  const range = useMemo(() => getMonthRange(visibleMonth), [visibleMonth]);

  const calendarQuery = useQuery({
    queryKey: ["calendar", range.startText, range.endText, "stats"],
    enabled: !!token,
    queryFn: () => fetchCalendar(token!, range.startText, range.endText),
  });

  async function refreshAll() {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["profile"] }),
      queryClient.invalidateQueries({ queryKey: ["ledgers"] }),
      queryClient.invalidateQueries({ queryKey: ["calendar"] }),
    ]);
  }

  const nickname = profileQuery.data?.nickname || "你";
  const aiEmoji = profileQuery.data?.ai_emoji || "✨";

  const monthRows = useMemo(() => {
    return (ledgersQuery.data || []).filter((item) => {
      const date = parseServerDate(item.transaction_date);
      return (
        !Number.isNaN(date.getTime()) &&
        date.getFullYear() === visibleMonth.getFullYear() &&
        date.getMonth() === visibleMonth.getMonth()
      );
    });
  }, [ledgersQuery.data, visibleMonth]);

  const expenseRows = useMemo(() => monthRows.filter((item) => getLedgerEntryKind(item) !== "income"), [monthRows]);
  const incomeRows = useMemo(() => monthRows.filter((item) => getLedgerEntryKind(item) === "income"), [monthRows]);
  const expenseTotal = useMemo(() => expenseRows.reduce((sum, item) => sum + Number(item.amount || 0), 0), [expenseRows]);
  const incomeTotal = useMemo(() => incomeRows.reduce((sum, item) => sum + Number(item.amount || 0), 0), [incomeRows]);
  const surplusTotal = incomeTotal - expenseTotal;
  const avgDaily = expenseRows.length > 0 ? expenseTotal / Math.max(1, new Date().getDate()) : 0;

  const topCategory = useMemo(() => {
    const bucket = new Map<string, number>();
    expenseRows.forEach((item) => {
      const key = getLedgerDisplayCategory(item.category) || "未分类";
      bucket.set(key, (bucket.get(key) || 0) + Number(item.amount || 0));
    });
    return Array.from(bucket.entries())
      .map(([name, value]) => ({ name, value }))
      .sort((a, b) => b.value - a.value)[0] || null;
  }, [expenseRows]);

  const mostExpensive = useMemo(() => {
    return expenseRows.reduce((max, item) => {
      if (!max || Number(item.amount || 0) > Number(max.amount || 0)) return item;
      return max;
    }, expenseRows[0]);
  }, [expenseRows]);

  const activeDays = useMemo(() => {
    const set = new Set<string>();
    monthRows.forEach((item) => set.add(item.transaction_date.slice(0, 10)));
    return set.size;
  }, [monthRows]);

  const categoryBreakdown = useMemo(() => {
    const bucket = new Map<string, number>();
    expenseRows.forEach((item) => {
      const key = getLedgerDisplayCategory(item.category) || "未分类";
      bucket.set(key, (bucket.get(key) || 0) + Number(item.amount || 0));
    });

    return Array.from(bucket.entries())
      .map(([label, value]) => ({
        label,
        value,
        share: expenseTotal > 0 ? value / expenseTotal : 0,
      }))
      .sort((a, b) => b.value - a.value)
      .slice(0, 5);
  }, [expenseRows, expenseTotal]);

  const weeklyTrend = useMemo<TrendPoint[]>(() => {
    const totals = [0, 0, 0, 0, 0];
    expenseRows.forEach((item) => {
      const day = parseServerDate(item.transaction_date).getDate();
      const index = Math.min(4, Math.floor((day - 1) / 7));
      totals[index] += Number(item.amount || 0);
    });

    return totals.map((value, index) => ({
      label: `第${index + 1}周`,
      value,
    }));
  }, [expenseRows]);

  const scheduleInsight = useMemo(() => {
    const now = Date.now();
    let pending = 0;
    let done = 0;
    const upcoming: Array<{ id: number; content: string; trigger_time: string }> = [];
    (calendarQuery.data?.days || []).forEach((day) => {
      day.schedules.forEach((item) => {
        const time = new Date(item.trigger_time).getTime();
        if (item.status === "EXECUTED") done += 1;
        else {
          pending += 1;
          if (!Number.isNaN(time) && time >= now) upcoming.push(item);
        }
      });
    });
    upcoming.sort((a, b) => new Date(a.trigger_time).getTime() - new Date(b.trigger_time).getTime());
    return { pending, done, nextThree: upcoming.slice(0, 3) };
  }, [calendarQuery.data?.days]);

  const scheduleCompletion = scheduleInsight.pending + scheduleInsight.done > 0
    ? scheduleInsight.done / (scheduleInsight.pending + scheduleInsight.done)
    : 0;

  const healthLabel =
    expenseTotal === 0
      ? "本月还没有账单记录"
      : avgDaily <= 50
        ? "支出节奏很稳"
        : avgDaily <= 120
          ? "本月消费适中"
          : "本月消费偏活跃";

  const tabBarWidth = pageWidth - spacing.pageX * 2;
  const tabIndicatorWidth = Math.max((tabBarWidth - 12) / 3, 0);
  const tabIndicatorTranslateX = scrollX.interpolate({
    inputRange: [0, pageWidth, pageWidth * 2],
    outputRange: [0, tabIndicatorWidth, tabIndicatorWidth * 2],
    extrapolate: "clamp",
  });
  const overviewTextColor = scrollX.interpolate({
    inputRange: [0, pageWidth, pageWidth * 2],
    outputRange: ["#ffffff", colors.text3, colors.text3],
  });
  const categoryTextColor = scrollX.interpolate({
    inputRange: [0, pageWidth, pageWidth * 2],
    outputRange: [colors.text3, "#ffffff", colors.text3],
  });
  const trendTextColor = scrollX.interpolate({
    inputRange: [0, pageWidth, pageWidth * 2],
    outputRange: [colors.text3, colors.text3, "#ffffff"],
  });
  const overviewParallax = scrollX.interpolate({
    inputRange: [0, pageWidth, pageWidth * 2],
    outputRange: [0, -pageWidth * 0.08, -pageWidth * 0.16],
  });
  const categoryParallax = scrollX.interpolate({
    inputRange: [0, pageWidth, pageWidth * 2],
    outputRange: [pageWidth * 0.08, 0, -pageWidth * 0.08],
  });
  const trendParallax = scrollX.interpolate({
    inputRange: [0, pageWidth, pageWidth * 2],
    outputRange: [pageWidth * 0.16, pageWidth * 0.08, 0],
  });

  function handleViewChange(nextView: "overview" | "category" | "trend") {
    setActiveView(nextView);
    const index = nextView === "overview" ? 0 : nextView === "category" ? 1 : 2;
    pagerRef.current?.scrollTo({ x: pageWidth * index, animated: true });
  }

  function handlePagerMomentumEnd(event: NativeSyntheticEvent<NativeScrollEvent>) {
    const index = Math.round(event.nativeEvent.contentOffset.x / pageWidth);
    setActiveView(index === 0 ? "overview" : index === 1 ? "category" : "trend");
  }

  const refreshing = profileQuery.isRefetching || ledgersQuery.isRefetching || calendarQuery.isRefetching;

  function renderOverviewPage() {
    return (
      <ScrollView
        style={styles.pageScroll}
        contentContainerStyle={[styles.content, { paddingBottom: bottomInset + 24 }]}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => void refreshAll()} />}
        showsVerticalScrollIndicator={false}
      >
        <Animated.View style={{ transform: [{ translateX: overviewParallax }] }}>
          <View style={styles.heroCard}>
            <View style={styles.heroGlowOne} />
            <View style={styles.heroGlowTwo} />
            <View style={styles.heroTop}>
              <View>
                <Text style={styles.heroEyebrow}>{getGreeting()} · {nickname}</Text>
                <Text style={styles.heroTitle}>统计中心</Text>
              </View>
              <Pressable style={styles.heroAvatarBtn} onPress={() => onNavigate("me")}>
                <Text style={styles.heroAvatarEmoji}>{aiEmoji}</Text>
              </Pressable>
            </View>
            <Text style={styles.heroDesc}>把账单、提醒和助手串成一页，用更像 app 的方式看全局状态。</Text>
            <View style={styles.heroMetrics}>
              <View style={styles.heroMetric}>
                <Text style={styles.heroMetricValue}>¥{expenseTotal.toFixed(0)}</Text>
                <Text style={styles.heroMetricLabel}>{formatMonthLabel(visibleMonth)}支出</Text>
              </View>
              <View style={styles.heroMetric}>
                <Text style={styles.heroMetricValue}>¥{incomeTotal.toFixed(0)}</Text>
                <Text style={styles.heroMetricLabel}>{formatMonthLabel(visibleMonth)}收入</Text>
              </View>
              <View style={styles.heroMetric}>
                <Text style={styles.heroMetricValue}>{scheduleInsight.pending}</Text>
                <Text style={styles.heroMetricLabel}>待办提醒</Text>
              </View>
            </View>
            <View style={styles.heroFooter}>
              <View style={styles.heroFooterChip}>
                <Ionicons name="pulse-outline" size={14} color="#fff" />
                <Text style={styles.heroFooterText}>{healthLabel}</Text>
              </View>
              <Text style={styles.heroFootnote}>{formatMonthLabel(visibleMonth)} · 富余 ¥{surplusTotal.toFixed(0)}</Text>
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
            <Text style={styles.sectionTitle}>当前节奏</Text>
            <View style={styles.insightGrid}>
              <View style={styles.insightCard}>
                <Text style={styles.insightLabel}>最高分类</Text>
                <Text style={styles.insightValue}>{topCategory?.name || "暂无"}</Text>
                <Text style={styles.insightMeta}>{topCategory ? `¥${topCategory.value.toFixed(0)}` : "等待数据"}</Text>
              </View>
              <View style={styles.insightCard}>
                <Text style={styles.insightLabel}>活跃天数</Text>
                <Text style={styles.insightValue}>{activeDays}</Text>
                <Text style={styles.insightMeta}>有记录的日期</Text>
              </View>
              <View style={styles.insightCardWide}>
                <Text style={styles.insightLabel}>单笔最高消费</Text>
                <Text style={styles.insightValue}>
                  {mostExpensive ? `¥${Number(mostExpensive.amount || 0).toFixed(0)}` : "—"}
                </Text>
                <Text style={styles.insightMeta}>
                  {mostExpensive
                    ? `${mostExpensive.item || "未命名"} · ${getLedgerDisplayCategory(mostExpensive.category) || "未分类"}`
                    : "暂无账单数据"}
                </Text>
              </View>
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
                  <Text style={styles.aiCardDesc}>自然语言下单、记账、查账、设提醒都走这里。</Text>
                </View>
                <View style={styles.aiCardArrow}>
                  <Ionicons name="arrow-forward" size={18} color="rgba(255,255,255,0.86)" />
                </View>
              </View>
            </Pressable>
          </View>
        </Animated.View>
      </ScrollView>
    );
  }

  function renderCategoryPage() {
    return (
      <ScrollView
        style={styles.pageScroll}
        contentContainerStyle={[styles.content, { paddingBottom: bottomInset + 24 }]}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => void refreshAll()} />}
        showsVerticalScrollIndicator={false}
      >
        <Animated.View style={{ transform: [{ translateX: categoryParallax }] }}>
          <StaggerGroup active={activeView === "category"} index={0}>
            <View style={styles.balanceHero}>
              <View style={styles.balanceMetric}>
                <Text style={styles.balanceLabel}>支出</Text>
                <Text style={styles.balanceValue}>¥{expenseTotal.toFixed(0)}</Text>
              </View>
              <View style={styles.balanceMetric}>
                <Text style={styles.balanceLabel}>收入</Text>
                <Text style={styles.balanceValue}>¥{incomeTotal.toFixed(0)}</Text>
              </View>
              <View style={styles.balanceMetric}>
                <Text style={styles.balanceLabel}>富余</Text>
                <Text style={[styles.balanceValue, surplusTotal >= 0 ? styles.balancePositive : styles.balanceNegative]}>
                  ¥{surplusTotal.toFixed(0)}
                </Text>
              </View>
            </View>
          </StaggerGroup>

          <StaggerGroup active={activeView === "category"} index={1}>
            <View style={styles.section}>
              <Text style={styles.sectionTitle}>分类占比</Text>
              <View style={styles.breakdownCard}>
                {categoryBreakdown.length === 0 ? (
                  <Text style={styles.breakdownEmpty}>本月还没有支出分类数据。</Text>
                ) : (
                  categoryBreakdown.map((item, index) => (
                    <View key={item.label} style={styles.breakdownRow}>
                      <View style={styles.breakdownTop}>
                        <View style={styles.breakdownLabelWrap}>
                          <View style={[styles.breakdownDot, { backgroundColor: BREAKDOWN_COLORS[index % BREAKDOWN_COLORS.length] }]} />
                          <Text style={styles.breakdownLabel}>{item.label}</Text>
                        </View>
                        <Text style={styles.breakdownValue}>¥{item.value.toFixed(0)}</Text>
                      </View>
                      <View style={styles.breakdownTrack}>
                        <View
                          style={[
                            styles.breakdownFill,
                            {
                              width: `${Math.max(8, item.share * 100)}%`,
                              backgroundColor: BREAKDOWN_COLORS[index % BREAKDOWN_COLORS.length],
                            },
                          ]}
                        />
                      </View>
                      <Text style={styles.breakdownShare}>{(item.share * 100).toFixed(0)}%</Text>
                    </View>
                  ))
                )}
              </View>
            </View>
          </StaggerGroup>

          <StaggerGroup active={activeView === "category"} index={2}>
            <View style={styles.section}>
              <Text style={styles.sectionTitle}>结构摘要</Text>
              <View style={styles.balanceGrid}>
                <View style={styles.balanceCard}>
                  <Text style={styles.balanceCardLabel}>收入覆盖率</Text>
                  <Text style={styles.balanceCardValue}>{expenseTotal > 0 ? `${((incomeTotal / expenseTotal) * 100).toFixed(0)}%` : "—"}</Text>
                  <Text style={styles.balanceCardMeta}>收入对本月支出的覆盖程度</Text>
                </View>
                <View style={styles.balanceCard}>
                  <Text style={styles.balanceCardLabel}>记账活跃度</Text>
                  <Text style={styles.balanceCardValue}>{activeDays} 天</Text>
                  <Text style={styles.balanceCardMeta}>本月有记录的天数</Text>
                </View>
              </View>
            </View>
          </StaggerGroup>
        </Animated.View>
      </ScrollView>
    );
  }

  function renderTrendPage() {
    const weeklyMax = Math.max(...weeklyTrend.map((item) => item.value), 1);

    return (
      <ScrollView
        style={styles.pageScroll}
        contentContainerStyle={[styles.content, { paddingBottom: bottomInset + 24 }]}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => void refreshAll()} />}
        showsVerticalScrollIndicator={false}
      >
        <Animated.View style={{ transform: [{ translateX: trendParallax }] }}>
          <StaggerGroup active={activeView === "trend"} index={0}>
            <View style={styles.section}>
              <Text style={styles.sectionTitle}>周趋势</Text>
              <View style={styles.trendCard}>
                {weeklyTrend.map((item) => (
                  <View key={item.label} style={styles.trendRow}>
                    <Text style={styles.trendLabel}>{item.label}</Text>
                    <View style={styles.trendTrack}>
                      <View
                        style={[
                          styles.trendFill,
                          { width: `${Math.max(8, (item.value / weeklyMax) * 100)}%` },
                        ]}
                      />
                    </View>
                    <Text style={styles.trendValue}>¥{item.value.toFixed(0)}</Text>
                  </View>
                ))}
              </View>
            </View>
          </StaggerGroup>

          <StaggerGroup active={activeView === "trend"} index={1}>
            <View style={styles.section}>
              <Text style={styles.sectionTitle}>近期提醒</Text>
              <View style={styles.timelineCard}>
                {scheduleInsight.nextThree.length === 0 ? (
                  <Text style={styles.timelineEmpty}>接下来没有未完成提醒，今天节奏不错。</Text>
                ) : (
                  scheduleInsight.nextThree.map((item, index) => (
                    <View key={item.id} style={[styles.timelineRow, index < scheduleInsight.nextThree.length - 1 && styles.timelineRowBorder]}>
                      <View style={styles.timelineDotWrap}>
                        <View style={styles.timelineDot} />
                      </View>
                      <View style={styles.timelineContent}>
                        <Text style={styles.timelineTitle}>{item.content}</Text>
                        <Text style={styles.timelineMeta}>{formatHmLocal(item.trigger_time)}</Text>
                      </View>
                      <Pressable style={styles.timelineAction} onPress={() => onNavigate("calendar")}>
                        <Ionicons name="arrow-forward" size={16} color={colors.primary} />
                      </Pressable>
                    </View>
                  ))
                )}
              </View>
            </View>
          </StaggerGroup>

          <StaggerGroup active={activeView === "trend"} index={2}>
            <View style={styles.section}>
              <Text style={styles.sectionTitle}>执行完成度</Text>
              <View style={styles.executionCard}>
                <View style={styles.executionHead}>
                  <Text style={styles.executionTitle}>提醒执行率</Text>
                  <Text style={styles.executionRate}>{(scheduleCompletion * 100).toFixed(0)}%</Text>
                </View>
                <View style={styles.executionTrack}>
                  <View style={[styles.executionFill, { width: `${Math.max(8, scheduleCompletion * 100)}%` }]} />
                </View>
                <View style={styles.executionMetaRow}>
                  <Text style={styles.executionMeta}>已完成 {scheduleInsight.done}</Text>
                  <Text style={styles.executionMeta}>待执行 {scheduleInsight.pending}</Text>
                </View>
              </View>
            </View>
          </StaggerGroup>
        </Animated.View>
      </ScrollView>
    );
  }

  return (
    <View style={styles.page}>
      <View style={[styles.headerShell, { paddingTop: insets.top + 12 }]}>
        <View style={styles.tabBar}>
          <Animated.View
            pointerEvents="none"
            style={[
              styles.tabIndicator,
              {
                width: tabIndicatorWidth,
                transform: [{ translateX: tabIndicatorTranslateX }],
              },
            ]}
          />
          <Pressable style={styles.tabItem} onPress={() => handleViewChange("overview")}>
            <Animated.Text style={[styles.tabText, { color: overviewTextColor }]}>总览</Animated.Text>
          </Pressable>
          <Pressable style={styles.tabItem} onPress={() => handleViewChange("category")}>
            <Animated.Text style={[styles.tabText, { color: categoryTextColor }]}>分类</Animated.Text>
          </Pressable>
          <Pressable style={styles.tabItem} onPress={() => handleViewChange("trend")}>
            <Animated.Text style={[styles.tabText, { color: trendTextColor }]}>趋势</Animated.Text>
          </Pressable>
        </View>
      </View>

      <Animated.ScrollView
        ref={pagerRef}
        style={styles.pager}
        horizontal
        pagingEnabled
        showsHorizontalScrollIndicator={false}
        onMomentumScrollEnd={handlePagerMomentumEnd}
        scrollEventThrottle={16}
        onScroll={Animated.event(
          [{ nativeEvent: { contentOffset: { x: scrollX } } }],
          { useNativeDriver: false }
        )}
      >
        <View style={[styles.pagerPage, { width: pageWidth }]}>{renderOverviewPage()}</View>
        <View style={[styles.pagerPage, { width: pageWidth }]}>{renderCategoryPage()}</View>
        <View style={[styles.pagerPage, { width: pageWidth }]}>{renderTrendPage()}</View>
      </Animated.ScrollView>
    </View>
  );
}

const BREAKDOWN_COLORS = ["#4f6ef7", "#10b981", "#f59e0b", "#ec4899", "#8b5cf6"];

const styles = StyleSheet.create({
  page: {
    flex: 1,
    backgroundColor: colors.bg,
  },
  headerShell: {
    paddingHorizontal: spacing.pageX,
    paddingBottom: 12,
  },
  tabBar: {
    position: "relative",
    flexDirection: "row",
    padding: 6,
    borderRadius: radii.lg,
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.borderLight,
    overflow: "hidden",
  },
  tabIndicator: {
    position: "absolute",
    top: 6,
    bottom: 6,
    left: 6,
    borderRadius: radii.md,
    backgroundColor: colors.primary,
    ...shadowSm,
  },
  tabItem: {
    flex: 1,
    zIndex: 1,
    alignItems: "center",
    justifyContent: "center",
    paddingVertical: 12,
    borderRadius: radii.md,
  },
  tabText: {
    fontSize: 14,
    fontWeight: "700",
    color: colors.text3,
  },
  pager: {
    flex: 1,
  },
  pagerPage: {
    flex: 1,
  },
  pageScroll: {
    flex: 1,
  },
  content: {
    paddingHorizontal: spacing.pageX,
    gap: 22,
  },
  heroCard: {
    overflow: "hidden",
    gap: 16,
    padding: 22,
    borderRadius: radii.xl,
    backgroundColor: colors.primary,
    ...shadowSm,
  },
  heroGlowOne: {
    position: "absolute",
    top: -24,
    right: -18,
    width: 120,
    height: 120,
    borderRadius: 999,
    backgroundColor: "rgba(255,255,255,0.14)",
  },
  heroGlowTwo: {
    position: "absolute",
    bottom: -34,
    left: -28,
    width: 146,
    height: 146,
    borderRadius: 999,
    backgroundColor: "rgba(255,255,255,0.1)",
  },
  heroTop: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
  },
  heroEyebrow: {
    fontSize: 13,
    fontWeight: "700",
    color: "rgba(255,255,255,0.75)",
  },
  heroTitle: {
    marginTop: 4,
    fontSize: 28,
    fontWeight: "800",
    color: "#fff",
  },
  heroAvatarBtn: {
    width: 54,
    height: 54,
    borderRadius: 27,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: "rgba(255,255,255,0.18)",
  },
  heroAvatarEmoji: {
    fontSize: 24,
  },
  heroDesc: {
    fontSize: 14,
    lineHeight: 21,
    color: "rgba(255,255,255,0.8)",
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
    backgroundColor: "rgba(255,255,255,0.12)",
  },
  heroMetricValue: {
    fontSize: 19,
    fontWeight: "800",
    color: "#fff",
  },
  heroMetricLabel: {
    fontSize: 12,
    color: "rgba(255,255,255,0.72)",
  },
  heroFooter: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: 10,
  },
  heroFooterChip: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    paddingHorizontal: 12,
    paddingVertical: 8,
    borderRadius: radii.full,
    backgroundColor: "rgba(255,255,255,0.12)",
  },
  heroFooterText: {
    fontSize: 12,
    fontWeight: "700",
    color: "#fff",
  },
  heroFootnote: {
    fontSize: 12,
    color: "rgba(255,255,255,0.74)",
  },
  section: {
    gap: 12,
  },
  sectionTitle: {
    fontSize: 18,
    fontWeight: "700",
    color: colors.text,
    paddingHorizontal: 2,
  },
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
  insightGrid: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 10,
  },
  insightCard: {
    width: "48%",
    flexGrow: 1,
    padding: 16,
    gap: 6,
    ...surfaceCard,
  },
  insightCardWide: {
    width: "100%",
    padding: 18,
    gap: 6,
    ...surfaceCard,
  },
  insightLabel: {
    fontSize: 12,
    fontWeight: "700",
    color: colors.text3,
  },
  insightValue: {
    fontSize: 22,
    fontWeight: "800",
    color: colors.text,
  },
  insightMeta: {
    fontSize: 12,
    lineHeight: 18,
    color: colors.text3,
  },
  aiCard: {
    overflow: "hidden",
    borderRadius: radii.xl,
    backgroundColor: colors.text,
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
    backgroundColor: "rgba(255,255,255,0.16)",
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
    lineHeight: 19,
    color: "rgba(255,255,255,0.74)",
    marginTop: 4,
  },
  aiCardArrow: {
    width: 36,
    height: 36,
    borderRadius: 18,
    backgroundColor: "rgba(255,255,255,0.1)",
    alignItems: "center",
    justifyContent: "center",
  },
  balanceHero: {
    flexDirection: "row",
    gap: 10,
    padding: 18,
    borderRadius: radii.xl,
    backgroundColor: colors.text,
    ...shadowSm,
  },
  balanceMetric: {
    flex: 1,
    gap: 6,
  },
  balanceLabel: {
    fontSize: 12,
    fontWeight: "700",
    color: "rgba(255,255,255,0.62)",
  },
  balanceValue: {
    fontSize: 24,
    fontWeight: "800",
    color: "#fff",
  },
  balancePositive: {
    color: "#86efac",
  },
  balanceNegative: {
    color: "#fecaca",
  },
  breakdownCard: {
    padding: 18,
    gap: 16,
    ...surfaceCard,
  },
  breakdownEmpty: {
    fontSize: 13,
    color: colors.text3,
  },
  breakdownRow: {
    gap: 8,
  },
  breakdownTop: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
  },
  breakdownLabelWrap: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
  },
  breakdownDot: {
    width: 10,
    height: 10,
    borderRadius: 999,
  },
  breakdownLabel: {
    fontSize: 14,
    fontWeight: "700",
    color: colors.text,
  },
  breakdownValue: {
    fontSize: 14,
    fontWeight: "700",
    color: colors.text2,
  },
  breakdownTrack: {
    height: 10,
    borderRadius: radii.full,
    backgroundColor: colors.borderLight,
    overflow: "hidden",
  },
  breakdownFill: {
    height: "100%",
    borderRadius: radii.full,
  },
  breakdownShare: {
    fontSize: 12,
    fontWeight: "700",
    color: colors.text3,
  },
  balanceGrid: {
    flexDirection: "row",
    gap: 10,
  },
  balanceCard: {
    flex: 1,
    padding: 16,
    gap: 6,
    ...surfaceCard,
  },
  balanceCardLabel: {
    fontSize: 12,
    fontWeight: "700",
    color: colors.text3,
  },
  balanceCardValue: {
    fontSize: 24,
    fontWeight: "800",
    color: colors.text,
  },
  balanceCardMeta: {
    fontSize: 12,
    lineHeight: 18,
    color: colors.text3,
  },
  trendCard: {
    padding: 18,
    gap: 14,
    ...surfaceCard,
  },
  trendRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
  },
  trendLabel: {
    width: 44,
    fontSize: 12,
    fontWeight: "700",
    color: colors.text3,
  },
  trendTrack: {
    flex: 1,
    height: 10,
    borderRadius: radii.full,
    backgroundColor: colors.borderLight,
    overflow: "hidden",
  },
  trendFill: {
    height: "100%",
    borderRadius: radii.full,
    backgroundColor: colors.primary,
  },
  trendValue: {
    width: 56,
    textAlign: "right",
    fontSize: 12,
    fontWeight: "700",
    color: colors.text2,
  },
  timelineCard: {
    paddingHorizontal: 16,
    ...surfaceCard,
  },
  timelineEmpty: {
    paddingVertical: 18,
    fontSize: 13,
    color: colors.text3,
  },
  timelineRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    paddingVertical: 14,
  },
  timelineRowBorder: {
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: colors.borderLight,
  },
  timelineDotWrap: {
    width: 18,
    alignItems: "center",
  },
  timelineDot: {
    width: 10,
    height: 10,
    borderRadius: 999,
    backgroundColor: colors.primary,
  },
  timelineContent: {
    flex: 1,
    gap: 3,
  },
  timelineTitle: {
    fontSize: 14,
    fontWeight: "700",
    color: colors.text,
  },
  timelineMeta: {
    fontSize: 12,
    color: colors.text3,
  },
  timelineAction: {
    width: 34,
    height: 34,
    borderRadius: 17,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: colors.primaryLight,
  },
  executionCard: {
    padding: 18,
    gap: 12,
    ...surfaceCard,
  },
  executionHead: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
  },
  executionTitle: {
    fontSize: 16,
    fontWeight: "700",
    color: colors.text,
  },
  executionRate: {
    fontSize: 24,
    fontWeight: "800",
    color: colors.primary,
  },
  executionTrack: {
    height: 12,
    borderRadius: radii.full,
    backgroundColor: colors.borderLight,
    overflow: "hidden",
  },
  executionFill: {
    height: "100%",
    borderRadius: radii.full,
    backgroundColor: colors.accent,
  },
  executionMetaRow: {
    flexDirection: "row",
    justifyContent: "space-between",
  },
  executionMeta: {
    fontSize: 12,
    fontWeight: "700",
    color: colors.text3,
  },
});
