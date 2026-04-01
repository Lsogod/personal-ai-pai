import { Ionicons } from "@expo/vector-icons";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  ActivityIndicator,
  Animated,
  NativeScrollEvent,
  NativeSyntheticEvent,
  RefreshControl,
  TextInput,
  ScrollView,
  StyleSheet,
  Text,
  Pressable,
  useWindowDimensions,
  View,
} from "react-native";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import Svg, { Circle } from "react-native-svg";

import {
  createLedger,
  encodeLedgerCategory,
  fetchLedgers,
  fetchLedgerStats,
  getLedgerDisplayCategory,
  getLedgerEntryKind,
  LedgerEntryKind,
  LedgerItem,
  updateLedger,
} from "../../lib/api";
import { formatDateLabel, formatHmLocal, formatMonthLabel, parseServerDate } from "../../lib/date";
import { useAuthStore } from "../../store/auth";
import { colors, radii, shadowMd, shadowSm, spacing, surfaceCard } from "../../design/tokens";
import { CreateLedgerModal } from "./CreateLedgerModal";
import { EditLedgerModal } from "./EditLedgerModal";

type LedgerTabProps = {
  bottomInset: number;
};

type DonutSegment = {
  label: string;
  value: number;
  color: string;
};

function CategoryDonutChart({
  segments,
  total,
}: {
  segments: DonutSegment[];
  total: number;
}) {
  const size = 164;
  const strokeWidth = 26;
  const radius = (size - strokeWidth) / 2;
  const circumference = 2 * Math.PI * radius;
  let offset = 0;

  return (
    <View style={styles.donutWrap}>
      <Svg width={size} height={size}>
        <Circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          stroke={colors.borderLight}
          strokeWidth={strokeWidth}
          fill="none"
        />
        {segments.map((segment) => {
          const dash = total > 0 ? (segment.value / total) * circumference : 0;
          const circle = (
            <Circle
              key={segment.label}
              cx={size / 2}
              cy={size / 2}
              r={radius}
              stroke={segment.color}
              strokeWidth={strokeWidth}
              fill="none"
              strokeDasharray={`${dash} ${circumference}`}
              strokeDashoffset={-offset}
              strokeLinecap="butt"
              rotation={-90}
              origin={`${size / 2}, ${size / 2}`}
            />
          );
          offset += dash;
          return circle;
        })}
      </Svg>
      <View pointerEvents="none" style={styles.donutCenter}>
        <Text style={styles.donutCenterLabel}>支出</Text>
        <Text style={styles.donutCenterValue}>¥{total.toFixed(0)}</Text>
      </View>
    </View>
  );
}

function StaggerGroup({
  active,
  index,
  children,
}: {
  active: boolean;
  index: number;
  children: React.ReactNode;
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

function isSameMonth(date: Date, monthDate: Date) {
  return date.getFullYear() === monthDate.getFullYear() && date.getMonth() === monthDate.getMonth();
}

export function LedgerTab({ bottomInset }: LedgerTabProps) {
  const token = useAuthStore((state) => state.token);
  const queryClient = useQueryClient();
  const insets = useSafeAreaInsets();
  const { width: pageWidth } = useWindowDimensions();
  const [activeTab, setActiveTab] = useState<"overview" | "list">("overview");
  const [createOpen, setCreateOpen] = useState(false);
  const [createInitialKind, setCreateInitialKind] = useState<LedgerEntryKind>("expense");
  const [editingLedger, setEditingLedger] = useState<LedgerItem | null>(null);
  const [incomeEditing, setIncomeEditing] = useState(false);
  const [incomeDraft, setIncomeDraft] = useState("");
  const [incomeNotice, setIncomeNotice] = useState<string | null>(null);
  const pagerRef = useRef<ScrollView | null>(null);
  const incomeInputRef = useRef<TextInput | null>(null);
  const scrollX = useRef(new Animated.Value(0)).current;

  const statsQuery = useQuery({
    queryKey: ["stats", "month"],
    enabled: !!token,
    queryFn: () => fetchLedgerStats(token!, "month"),
  });

  const ledgersQuery = useQuery({
    queryKey: ["ledgers"],
    enabled: !!token,
    queryFn: () => fetchLedgers(token!, 200),
  });

  const visibleMonth = useMemo(() => {
    const today = new Date();
    const fallback = new Date(today.getFullYear(), today.getMonth(), 1);
    const rows = ledgersQuery.data || [];
    if (rows.length === 0) return fallback;

    const hasCurrentMonth = rows.some((item) => {
      const date = parseServerDate(item.transaction_date);
      return !Number.isNaN(date.getTime()) && isSameMonth(date, fallback);
    });
    if (hasCurrentMonth) return fallback;

    const latest = rows
      .map((item) => parseServerDate(item.transaction_date))
      .filter((item) => !Number.isNaN(item.getTime()))
      .sort((a, b) => b.getTime() - a.getTime())[0];

    return latest ? new Date(latest.getFullYear(), latest.getMonth(), 1) : fallback;
  }, [ledgersQuery.data]);

  const monthRows = useMemo(
    () =>
      (ledgersQuery.data || []).filter((item) => {
        const date = parseServerDate(item.transaction_date);
        return !Number.isNaN(date.getTime()) && isSameMonth(date, visibleMonth);
      }),
    [ledgersQuery.data, visibleMonth]
  );

  const incomeRows = useMemo(() => monthRows.filter((item) => getLedgerEntryKind(item) === "income"), [monthRows]);
  const expenseRows = useMemo(() => monthRows.filter((item) => getLedgerEntryKind(item) !== "income"), [monthRows]);
  const managedIncomeLedger = useMemo(() => {
    const preferredTitle = `${formatMonthLabel(visibleMonth)}收入`;
    return (
      incomeRows.find((item) => String(item.item || "").trim() === preferredTitle) ||
      incomeRows.find((item) => (getLedgerDisplayCategory(item.category) || "") === "收入") ||
      incomeRows[0] ||
      null
    );
  }, [incomeRows, visibleMonth]);
  const otherIncomeTotal = useMemo(
    () =>
      incomeRows.reduce((sum, item) => {
        if (managedIncomeLedger && item.id === managedIncomeLedger.id) return sum;
        return sum + Number(item.amount || 0);
      }, 0),
    [incomeRows, managedIncomeLedger]
  );
  const incomeTotal = useMemo(
    () => incomeRows.reduce((sum, item) => sum + Number(item.amount || 0), 0),
    [incomeRows]
  );
  const expenseTotal = useMemo(
    () => expenseRows.reduce((sum, item) => sum + Number(item.amount || 0), 0),
    [expenseRows]
  );
  const surplusTotal = useMemo(() => incomeTotal - expenseTotal, [expenseTotal, incomeTotal]);

  const categoryStats = useMemo(() => {
    const stats = new Map<string, number>();
    expenseRows.forEach((item) => {
      const key = getLedgerDisplayCategory(item.category) || "未分类";
      stats.set(key, (stats.get(key) || 0) + Number(item.amount || 0));
    });
    return Array.from(stats.entries())
      .map(([name, value]) => ({ name, value }))
      .sort((a, b) => b.value - a.value)
      .slice(0, 5);
  }, [expenseRows]);

  const groupedRows = useMemo(() => groupByDay(ledgersQuery.data || []), [ledgersQuery.data]);
  const avgDaily = useMemo(() => {
    const today = Math.max(1, new Date().getDate());
    return (expenseTotal / today).toFixed(0);
  }, [expenseTotal]);
  const overviewInsight = useMemo(() => {
    const totalSpend = expenseTotal;
    const categoryLeader = categoryStats[0] || null;
    const leaderShare = categoryLeader && totalSpend > 0 ? (categoryLeader.value / totalSpend) * 100 : 0;
    const biggestExpense = expenseRows.reduce((max, item) => {
      if (!max || Number(item.amount || 0) > Number(max.amount || 0)) return item;
      return max;
    }, expenseRows[0]);
    const busiestDay = groupByDay(expenseRows).sort((a, b) => b.total - a.total)[0] || null;
    const today = new Date();
    const monthDays = new Date(today.getFullYear(), today.getMonth() + 1, 0).getDate();
    return {
      categoryLeader,
      leaderShare,
      biggestExpense,
      busiestDay,
      projected: Number(avgDaily) * monthDays,
    };
  }, [avgDaily, categoryStats, expenseRows, expenseTotal]);

  const donutSegments = useMemo(() => {
    if (categoryStats.length === 0) return [];
    const top = categoryStats.slice(0, 4).map((item, index) => ({
      label: item.name,
      value: item.value,
      color: CATEGORY_COLORS[index % CATEGORY_COLORS.length],
    }));
    const otherValue = categoryStats.slice(4).reduce((sum, item) => sum + item.value, 0);
    if (otherValue > 0) {
      top.push({
        label: "其他",
        value: otherValue,
        color: "#d7ddea",
      });
    }
    return top;
  }, [categoryStats]);

  async function refreshAll() {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["stats", "month"] }),
      queryClient.invalidateQueries({ queryKey: ["ledgers"] }),
      queryClient.invalidateQueries({ queryKey: ["calendar"] }),
    ]);
  }

  async function handleCreated() {
    await refreshAll();
  }

  useEffect(() => {
    if (!incomeEditing) return;
    setIncomeDraft(incomeTotal > 0 ? String(Number(incomeTotal.toFixed(2))) : "");
    setIncomeNotice(null);
    const timer = setTimeout(() => {
      incomeInputRef.current?.focus();
    }, 60);
    return () => clearTimeout(timer);
  }, [incomeEditing, incomeTotal]);

  const saveIncomeMutation = useMutation({
    mutationFn: async () => {
      const desiredTotal = Number(incomeDraft.replace(/,/g, ".").trim());
      if (!Number.isFinite(desiredTotal) || desiredTotal < 0) {
        throw new Error("请输入大于等于 0 的收入金额。");
      }
      if (desiredTotal < otherIncomeTotal) {
        throw new Error(`当前还有其他收入记录 ¥${otherIncomeTotal.toFixed(0)}，收入汇总不能低于这个值。`);
      }

      const nextManagedAmount = Number((desiredTotal - otherIncomeTotal).toFixed(2));
      const monthIncomeTitle = `${formatMonthLabel(visibleMonth)}收入`;
      const category =
        encodeLedgerCategory(getLedgerDisplayCategory(managedIncomeLedger?.category) || "收入", "income") || "收入:收入";
      const item = String(managedIncomeLedger?.item || monthIncomeTitle).trim() || monthIncomeTitle;

      if (managedIncomeLedger) {
        await updateLedger(
          managedIncomeLedger.id,
          {
            amount: nextManagedAmount,
            category,
            item,
          },
          token!
        );
        return;
      }

      if (nextManagedAmount <= 0) {
        return;
      }

      await createLedger(
        {
          amount: nextManagedAmount,
          category,
          item,
        },
        token!
      );
    },
    onSuccess: async () => {
      await handleCreated();
      setIncomeEditing(false);
      setIncomeNotice(null);
    },
    onError: (error: Error) => {
      setIncomeNotice(error.message);
    },
  });

  function openCreate(kind: LedgerEntryKind) {
    setCreateInitialKind(kind);
    setCreateOpen(true);
  }

  function beginIncomeEdit() {
    setIncomeEditing(true);
  }

  function cancelIncomeEdit() {
    if (saveIncomeMutation.isPending) return;
    setIncomeEditing(false);
    setIncomeDraft(incomeTotal > 0 ? String(Number(incomeTotal.toFixed(2))) : "");
    setIncomeNotice(null);
  }

  const tabBarWidth = pageWidth - spacing.pageX * 2;
  const tabIndicatorWidth = Math.max((tabBarWidth - 12) / 2, 0);
  const tabIndicatorTranslateX = scrollX.interpolate({
    inputRange: [0, pageWidth],
    outputRange: [0, tabIndicatorWidth],
    extrapolate: "clamp",
  });
  const overviewTextColor = scrollX.interpolate({
    inputRange: [0, pageWidth],
    outputRange: ["#ffffff", colors.text3],
    extrapolate: "clamp",
  });
  const listTextColor = scrollX.interpolate({
    inputRange: [0, pageWidth],
    outputRange: [colors.text3, "#ffffff"],
    extrapolate: "clamp",
  });
  const overviewParallax = scrollX.interpolate({
    inputRange: [0, pageWidth],
    outputRange: [0, -pageWidth * 0.08],
    extrapolate: "clamp",
  });
  const listParallax = scrollX.interpolate({
    inputRange: [0, pageWidth],
    outputRange: [pageWidth * 0.08, 0],
    extrapolate: "clamp",
  });

  function handleTabChange(nextTab: "overview" | "list") {
    setActiveTab(nextTab);
    pagerRef.current?.scrollTo({ x: nextTab === "overview" ? 0 : pageWidth, animated: true });
  }

  function handlePagerMomentumEnd(event: NativeSyntheticEvent<NativeScrollEvent>) {
    const x = event.nativeEvent.contentOffset.x;
    setActiveTab(x >= pageWidth / 2 ? "list" : "overview");
  }

  function renderOverviewPage() {
    return (
      <ScrollView
        style={styles.pageScroll}
        contentContainerStyle={[styles.pageContent, { paddingBottom: bottomInset + 126 }]}
        refreshControl={<RefreshControl refreshing={ledgersQuery.isRefetching} onRefresh={() => void refreshAll()} />}
        showsVerticalScrollIndicator={false}
      >
        <Animated.View style={{ transform: [{ translateX: overviewParallax }] }}>
          <View style={styles.summaryCard}>
            <View style={styles.summaryGlow} />
            <Text style={styles.summaryLabel}>{formatMonthLabel(visibleMonth)}概览</Text>
            <View style={styles.summaryGrid}>
              <View style={styles.metricCard}>
                <Text style={styles.metricValue}>¥{expenseTotal.toFixed(0)}</Text>
                <Text style={styles.metricLabel}>支出</Text>
              </View>
              <View style={[styles.metricCard, styles.metricCardInteractive, incomeEditing && styles.metricCardEditing]}>
                <View style={styles.metricHead}>
                  {incomeEditing ? (
                    <View style={styles.metricInputRow}>
                      <Text style={styles.metricCurrency}>¥</Text>
                      <TextInput
                        ref={incomeInputRef}
                        value={incomeDraft}
                        onChangeText={(value) => {
                          setIncomeDraft(value);
                          setIncomeNotice(null);
                        }}
                        keyboardType="decimal-pad"
                        placeholder="0"
                        placeholderTextColor="rgba(255,255,255,0.5)"
                        style={styles.metricInput}
                        selectionColor="#ffffff"
                      />
                    </View>
                  ) : (
                    <Text style={styles.metricValue}>¥{incomeTotal.toFixed(0)}</Text>
                  )}
                  <View style={styles.metricActionRow}>
                    {incomeEditing ? (
                      <>
                        <Pressable
                          style={[styles.metricActionBtn, styles.metricActionBtnSolid]}
                          disabled={saveIncomeMutation.isPending}
                          onPress={() => void saveIncomeMutation.mutateAsync()}
                        >
                          {saveIncomeMutation.isPending ? (
                            <ActivityIndicator size="small" color={colors.accent} />
                          ) : (
                            <Ionicons name="checkmark" size={15} color={colors.accent} />
                          )}
                        </Pressable>
                        <Pressable
                          style={[styles.metricActionBtn, styles.metricActionBtnGhost]}
                          disabled={saveIncomeMutation.isPending}
                          onPress={cancelIncomeEdit}
                        >
                          <Ionicons name="close" size={15} color="#ffffff" />
                        </Pressable>
                      </>
                    ) : (
                      <Pressable style={styles.metricActionBtn} onPress={beginIncomeEdit}>
                        <Ionicons name="create-outline" size={14} color={colors.accent} />
                      </Pressable>
                    )}
                  </View>
                </View>
                <Text style={styles.metricLabel}>收入</Text>
                <Text style={[styles.metricHint, incomeNotice ? styles.metricHintError : null]}>
                  {incomeNotice || (incomeEditing ? "直接在这里修改金额并保存" : "点右上角编辑图标修改")}
                </Text>
              </View>
              <View style={styles.metricCard}>
                <Text style={styles.metricValue}>¥{surplusTotal.toFixed(0)}</Text>
                <Text style={styles.metricLabel}>富余</Text>
              </View>
              <View style={styles.metricCard}>
                <Text style={styles.metricValue}>{monthRows.length || Number(statsQuery.data?.count || 0)}</Text>
                <Text style={styles.metricLabel}>笔数</Text>
              </View>
            </View>
            <Text style={styles.summaryFootnote}>当前查看 {formatMonthLabel(visibleMonth)} · 日均支出 ¥{avgDaily}</Text>
          </View>

          <View style={styles.highlightRow}>
            <View style={styles.highlightCard}>
              <Text style={styles.highlightLabel}>最高分类</Text>
              <Text style={styles.highlightValue}>{overviewInsight.categoryLeader?.name || "暂无"}</Text>
              <Text style={styles.highlightMeta}>
                {overviewInsight.categoryLeader
                  ? `占比 ${overviewInsight.leaderShare.toFixed(0)}%`
                  : "等待本月账单"}
              </Text>
            </View>
            <View style={styles.highlightCard}>
              <Text style={styles.highlightLabel}>单日峰值</Text>
              <Text style={styles.highlightValue}>
                {overviewInsight.busiestDay ? `¥${overviewInsight.busiestDay.total.toFixed(0)}` : "—"}
              </Text>
              <Text style={styles.highlightMeta}>
                {overviewInsight.busiestDay?.label || "暂无高峰日"}
              </Text>
            </View>
          </View>

          <View style={styles.card}>
            <Text style={styles.cardTitle}>分类占比</Text>
            {donutSegments.length === 0 ? (
              <Text style={styles.emptyText}>本月暂无账单数据</Text>
            ) : (
              <View style={styles.chartSection}>
                <CategoryDonutChart segments={donutSegments} total={expenseTotal} />
                <View style={styles.chartLegend}>
                  {donutSegments.map((item) => (
                    <View key={item.label} style={styles.categoryWrap}>
                      <View style={styles.categoryRow}>
                        <View style={[styles.categoryDot, { backgroundColor: item.color }]} />
                        <Text style={styles.categoryName}>{item.label}</Text>
                        <Text style={styles.categoryValue}>¥{item.value.toFixed(0)}</Text>
                      </View>
                      <View style={styles.categoryBarTrack}>
                        <View
                          style={[
                            styles.categoryBarFill,
                            {
                              width: `${Math.max(8, expenseTotal > 0 ? (item.value / expenseTotal) * 100 : 0)}%`,
                              backgroundColor: item.color,
                            },
                          ]}
                        />
                      </View>
                    </View>
                  ))}
                </View>
              </View>
            )}
          </View>

          <View style={styles.card}>
            <Text style={styles.cardTitle}>消费提示</Text>
            <View style={styles.tipRow}>
              <View style={styles.tipBadge}>
                <Ionicons name="pulse-outline" size={16} color={colors.primary} />
              </View>
              <View style={styles.tipContent}>
                <Text style={styles.tipTitle}>月末预测</Text>
                <Text style={styles.tipText}>按当前节奏，本月支出预计约 ¥{overviewInsight.projected.toFixed(0)}</Text>
              </View>
            </View>
            <View style={styles.tipRow}>
              <View style={[styles.tipBadge, { backgroundColor: colors.accentLight }]}>
                <Ionicons name="flash-outline" size={16} color={colors.accent} />
              </View>
              <View style={styles.tipContent}>
                <Text style={styles.tipTitle}>单笔峰值</Text>
                <Text style={styles.tipText}>
                  {overviewInsight.biggestExpense
                    ? `${overviewInsight.biggestExpense.item || "未命名消费"}，¥${Number(overviewInsight.biggestExpense.amount || 0).toFixed(0)}`
                    : "本月还没有记录高额消费"}
                </Text>
              </View>
            </View>
          </View>

          <View style={styles.card}>
            <Text style={styles.cardTitle}>最近记录</Text>
            {monthRows.slice(0, 6).map((item) => (
              <Pressable key={item.id} style={styles.ledgerRow} onPress={() => setEditingLedger(item)}>
                <View style={styles.ledgerMain}>
                  <Text style={styles.ledgerName}>{item.item || "未命名账单"}</Text>
                  <Text style={styles.ledgerMeta}>
                    {getLedgerDisplayCategory(item.category) || "未分类"} · {formatHmLocal(item.transaction_date)}
                  </Text>
                </View>
                <Ionicons name="create-outline" size={16} color={colors.text4} />
                <Text style={[styles.ledgerAmount, getLedgerEntryKind(item) === "income" && styles.incomeAmount]}>
                  {getLedgerEntryKind(item) === "income" ? "+" : "-"}¥{Number(item.amount || 0).toFixed(0)}
                </Text>
              </Pressable>
            ))}
          </View>
        </Animated.View>
      </ScrollView>
    );
  }

  function renderListPage() {
    return (
      <ScrollView
        style={styles.pageScroll}
        contentContainerStyle={[styles.pageContent, { paddingBottom: bottomInset + 126 }]}
        refreshControl={<RefreshControl refreshing={ledgersQuery.isRefetching} onRefresh={() => void refreshAll()} />}
        showsVerticalScrollIndicator={false}
      >
        <Animated.View style={{ transform: [{ translateX: listParallax }] }}>
          <View style={styles.card}>
            <View style={styles.listHead}>
              <Text style={styles.cardTitle}>账单明细</Text>
              <Text style={styles.listMeta}>共 {(ledgersQuery.data || []).length} 条</Text>
            </View>
            {groupedRows.map((group, index) => (
              <StaggerGroup key={group.date} active={activeTab === "list"} index={index}>
                <View style={styles.groupWrap}>
                  <View style={styles.groupHead}>
                    <Text style={styles.groupTitle}>{group.label}</Text>
                    <Text style={styles.groupMeta}>¥{group.total.toFixed(0)}</Text>
                  </View>
                  {group.rows.map((row) => (
                    <Pressable key={row.id} style={styles.billItem} onPress={() => setEditingLedger(row)}>
                      <View style={styles.billTop}>
                        <Text style={styles.billName}>{row.item || "未命名"}</Text>
                        <Text style={[styles.billAmount, getLedgerEntryKind(row) === "income" && styles.incomeAmount]}>
                          {getLedgerEntryKind(row) === "income" ? "+" : "-"}¥{Number(row.amount || 0).toFixed(0)}
                        </Text>
                      </View>
                      <View style={styles.billBottom}>
                        <Text style={styles.billMeta}>
                          {getLedgerDisplayCategory(row.category) || "未分类"} · {formatHmLocal(row.transaction_date)}
                        </Text>
                        <Ionicons name="ellipsis-horizontal" size={16} color={colors.text4} />
                      </View>
                    </Pressable>
                  ))}
                </View>
              </StaggerGroup>
            ))}
          </View>
        </Animated.View>
      </ScrollView>
    );
  }

  return (
  <>
    <View style={styles.page}>
      <View style={[styles.headerShell, { paddingTop: insets.top + 8 }]}>
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
          <Pressable style={styles.tabItem} onPress={() => handleTabChange("overview")}>
            <Animated.Text style={[styles.tabText, { color: overviewTextColor }]}>概览</Animated.Text>
          </Pressable>
          <Pressable style={styles.tabItem} onPress={() => handleTabChange("list")}>
            <Animated.Text style={[styles.tabText, { color: listTextColor }]}>明细</Animated.Text>
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
        <View style={[styles.pagerPage, { width: pageWidth }]}>
          {renderOverviewPage()}
        </View>
        <View style={[styles.pagerPage, { width: pageWidth }]}>
          {renderListPage()}
        </View>
      </Animated.ScrollView>
    </View>

    <Pressable
      style={[styles.fab, { bottom: bottomInset + 24 }]}
      onPress={() => openCreate("expense")}
    >
      <Ionicons name="add" size={24} color="#fff" />
    </Pressable>

    <CreateLedgerModal
      visible={createOpen}
      token={token}
      initialKind={createInitialKind}
      onClose={() => setCreateOpen(false)}
      onCreated={handleCreated}
    />

    <EditLedgerModal
      visible={!!editingLedger}
      token={token}
      ledger={editingLedger}
      onClose={() => setEditingLedger(null)}
      onChanged={handleCreated}
    />
  </>
  );
}

const CATEGORY_COLORS = ["#4f6ef7", "#10b981", "#f59e0b", "#ec4899", "#8b5cf6"];

const styles = StyleSheet.create({
  page: {
    flex: 1,
    backgroundColor: colors.bg,
  },
  headerShell: {
    paddingHorizontal: spacing.pageX,
    paddingBottom: 12,
  },
  pageContent: {
    paddingHorizontal: spacing.pageX,
    paddingTop: 4,
    gap: 14,
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
  pager: {
    flex: 1,
  },
  pagerPage: {
    flex: 1,
  },
  pageScroll: {
    flex: 1,
  },
  summaryCard: {
    overflow: "hidden",
    borderRadius: radii.xl,
    backgroundColor: colors.primary,
    paddingHorizontal: 22,
    paddingVertical: 20,
    ...shadowMd,
  },
  summaryGlow: {
    position: "absolute",
    right: -30,
    bottom: -38,
    width: 160,
    height: 160,
    borderRadius: 999,
    backgroundColor: "rgba(255,255,255,0.1)",
  },
  summaryLabel: {
    fontSize: 13,
    fontWeight: "700",
    color: "rgba(255,255,255,0.84)",
    marginBottom: 12,
  },
  summaryGrid: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 10,
  },
  metricCard: {
    width: "47%",
    flexGrow: 1,
    paddingVertical: 14,
    paddingHorizontal: 12,
    borderRadius: radii.md,
    backgroundColor: "rgba(255,255,255,0.12)",
    gap: 4,
  },
  metricCardInteractive: {
    borderWidth: 1,
    borderColor: "rgba(255,255,255,0.16)",
  },
  metricCardEditing: {
    backgroundColor: "rgba(255,255,255,0.16)",
  },
  metricHead: {
    flexDirection: "row",
    alignItems: "flex-start",
    justifyContent: "space-between",
    gap: 8,
  },
  metricInputRow: {
    flex: 1,
    flexDirection: "row",
    alignItems: "center",
    gap: 2,
  },
  metricCurrency: {
    fontSize: 24,
    fontWeight: "800",
    color: "#ffffff",
  },
  metricInput: {
    flex: 1,
    minWidth: 72,
    paddingVertical: 0,
    fontSize: 24,
    fontWeight: "800",
    color: "#ffffff",
  },
  metricActionRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
  },
  metricActionBtn: {
    width: 24,
    height: 24,
    borderRadius: 12,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: "rgba(255,255,255,0.92)",
  },
  metricActionBtnSolid: {
    backgroundColor: "rgba(255,255,255,0.96)",
  },
  metricActionBtnGhost: {
    backgroundColor: "rgba(255,255,255,0.22)",
    borderWidth: 1,
    borderColor: "rgba(255,255,255,0.18)",
  },
  summaryFootnote: {
    marginTop: 12,
    fontSize: 12,
    color: "rgba(255,255,255,0.76)",
  },
  highlightRow: {
    flexDirection: "row",
    gap: 10,
  },
  highlightCard: {
    flex: 1,
    padding: 18,
    gap: 8,
    ...surfaceCard,
  },
  highlightLabel: {
    fontSize: 12,
    fontWeight: "700",
    color: colors.text3,
    textTransform: "uppercase" as const,
    letterSpacing: 0.5,
  },
  highlightValue: {
    fontSize: 22,
    fontWeight: "800",
    color: colors.text,
  },
  highlightMeta: {
    fontSize: 12,
    color: colors.text3,
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
  metricHint: {
    fontSize: 11,
    color: "rgba(255,255,255,0.68)",
  },
  metricHintError: {
    color: "#ffe2e2",
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
  chartSection: {
    flexDirection: "row",
    alignItems: "center",
    gap: 16,
  },
  donutWrap: {
    width: 164,
    height: 164,
    alignItems: "center",
    justifyContent: "center",
  },
  donutCenter: {
    position: "absolute",
    alignItems: "center",
    gap: 3,
  },
  donutCenterLabel: {
    fontSize: 12,
    fontWeight: "700",
    color: colors.text3,
  },
  donutCenterValue: {
    fontSize: 20,
    fontWeight: "800",
    color: colors.text,
  },
  chartLegend: {
    flex: 1,
    gap: 12,
  },
  emptyText: {
    fontSize: 13,
    color: colors.text3,
  },
  categoryWrap: {
    gap: 8,
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
  categoryBarTrack: {
    height: 8,
    borderRadius: radii.full,
    backgroundColor: colors.borderLight,
    overflow: "hidden",
  },
  categoryBarFill: {
    height: "100%",
    borderRadius: radii.full,
  },
  tipRow: {
    flexDirection: "row",
    gap: 12,
    alignItems: "flex-start",
  },
  tipBadge: {
    width: 34,
    height: 34,
    borderRadius: 17,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: colors.primaryLight,
  },
  tipContent: {
    flex: 1,
    gap: 4,
  },
  tipTitle: {
    fontSize: 14,
    fontWeight: "700",
    color: colors.text,
  },
  tipText: {
    fontSize: 12,
    lineHeight: 18,
    color: colors.text3,
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
  incomeAmount: {
    color: colors.accent,
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
  fab: {
    position: "absolute",
    right: 20,
    width: 58,
    height: 58,
    borderRadius: 29,
    backgroundColor: colors.primary,
    alignItems: "center",
    justifyContent: "center",
    ...shadowMd,
  },
});
