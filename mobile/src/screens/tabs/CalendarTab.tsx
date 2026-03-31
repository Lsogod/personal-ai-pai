import { Ionicons } from "@expo/vector-icons";
import { type ReactNode, useEffect, useMemo, useRef, useState } from "react";
import {
  Animated,
  Alert,
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
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import {
  CalendarDay,
  CalendarLedgerItem,
  CalendarScheduleItem,
  fetchCalendar,
  fetchLedgers,
  fetchSchedules,
  getLedgerDisplayCategory,
  getLedgerEntryKind,
  LedgerItem,
  ScheduleItem,
  updateSchedule,
} from "../../lib/api";
import {
  addMonths,
  buildCalendarGrid,
  formatDateLabel,
  formatHmLocal,
  formatMonthLabel,
  getMonthRange,
  getScheduleStatusLabel,
  parseServerDate,
} from "../../lib/date";
import { colors, radii, shadowSm, spacing, surfaceCard } from "../../design/tokens";
import { useAuthStore } from "../../store/auth";
import { CreateLedgerModal } from "./CreateLedgerModal";
import { CreateScheduleModal } from "./CreateScheduleModal";
import { EditLedgerModal } from "./EditLedgerModal";
import { EditScheduleModal } from "./EditScheduleModal";

type CalendarTabProps = {
  bottomInset: number;
};

const WEEK_HEADERS = ["一", "二", "三", "四", "五", "六", "日"];

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
          duration: 260,
          delay: index * 80,
          useNativeDriver: true,
        }),
        Animated.timing(translateY, {
          toValue: 0,
          duration: 300,
          delay: index * 80,
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

export function CalendarTab({ bottomInset }: CalendarTabProps) {
  const token = useAuthStore((state) => state.token);
  const queryClient = useQueryClient();
  const insets = useSafeAreaInsets();
  const { width: pageWidth } = useWindowDimensions();
  const [anchorMonth, setAnchorMonth] = useState(() => new Date(new Date().getFullYear(), new Date().getMonth(), 1));
  const [activeDate, setActiveDate] = useState("");
  const [activeView, setActiveView] = useState<"calendar" | "list">("calendar");
  const [createLedgerOpen, setCreateLedgerOpen] = useState(false);
  const [createScheduleOpen, setCreateScheduleOpen] = useState(false);
  const [editingLedger, setEditingLedger] = useState<LedgerItem | null>(null);
  const [editingSchedule, setEditingSchedule] = useState<ScheduleItem | null>(null);
  const pagerRef = useRef<ScrollView | null>(null);
  const scrollX = useRef(new Animated.Value(0)).current;
  const autoMonthAdjustedRef = useRef(false);

  const range = useMemo(() => getMonthRange(anchorMonth), [anchorMonth]);

  const calendarQuery = useQuery({
    queryKey: ["calendar", range.startText, range.endText],
    enabled: !!token,
    queryFn: () => fetchCalendar(token!, range.startText, range.endText),
  });

  const ledgersQuery = useQuery({
    queryKey: ["ledgers", "calendar-anchor"],
    enabled: !!token,
    queryFn: () => fetchLedgers(token!, 80),
  });

  const schedulesQuery = useQuery({
    queryKey: ["schedules", "calendar-anchor"],
    enabled: !!token,
    queryFn: () => fetchSchedules(token!, 80),
  });

  useEffect(() => {
    const today = new Date();
    if (today.getFullYear() === anchorMonth.getFullYear() && today.getMonth() === anchorMonth.getMonth()) {
      setActiveDate(`${today.getFullYear()}-${`${today.getMonth() + 1}`.padStart(2, "0")}-${`${today.getDate()}`.padStart(2, "0")}`);
      return;
    }
    setActiveDate(range.startText);
  }, [anchorMonth, range.startText]);

  useEffect(() => {
    if (autoMonthAdjustedRef.current) return;

    const currentMonth = new Date(new Date().getFullYear(), new Date().getMonth(), 1);
    if (anchorMonth.getFullYear() !== currentMonth.getFullYear() || anchorMonth.getMonth() !== currentMonth.getMonth()) {
      autoMonthAdjustedRef.current = true;
      return;
    }

    const activityDates = [
      ...(ledgersQuery.data || []).map((item) => parseServerDate(item.transaction_date)),
      ...(schedulesQuery.data || []).map((item) => parseServerDate(item.trigger_time)),
    ].filter((item) => !Number.isNaN(item.getTime()));

    if (activityDates.length === 0) {
      autoMonthAdjustedRef.current = true;
      return;
    }

    const hasCurrentMonthActivity = activityDates.some(
      (item) => item.getFullYear() === currentMonth.getFullYear() && item.getMonth() === currentMonth.getMonth()
    );
    if (hasCurrentMonthActivity) {
      autoMonthAdjustedRef.current = true;
      return;
    }

    const latest = activityDates.sort((a, b) => b.getTime() - a.getTime())[0];
    autoMonthAdjustedRef.current = true;
    setAnchorMonth(new Date(latest.getFullYear(), latest.getMonth(), 1));
  }, [anchorMonth, ledgersQuery.data, schedulesQuery.data]);

  async function refreshAll() {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["calendar"] }),
      queryClient.invalidateQueries({ queryKey: ["ledgers"] }),
      queryClient.invalidateQueries({ queryKey: ["schedules"] }),
      queryClient.invalidateQueries({ queryKey: ["stats", "month"] }),
    ]);
  }

  const calendarDays = calendarQuery.data?.days || [];
  const grid = useMemo(() => buildCalendarGrid(calendarDays, anchorMonth), [anchorMonth, calendarDays]);

  const activeDay: CalendarDay =
    calendarDays.find((item) => item.date === activeDate) || {
      date: activeDate,
      ledger_total: 0,
      ledger_count: 0,
      schedule_count: 0,
      ledgers: [],
      schedules: [],
    };

  const monthStats = useMemo(() => {
    const allLedgers = calendarDays.flatMap((day) => day.ledgers);
    const totalSpend = allLedgers
      .filter((item) => getLedgerEntryKind(item) !== "income")
      .reduce((sum, item) => sum + Number(item.amount || 0), 0);
    const totalIncome = allLedgers
      .filter((item) => getLedgerEntryKind(item) === "income")
      .reduce((sum, item) => sum + Number(item.amount || 0), 0);
    let scheduleDone = 0;
    let schedulePending = 0;
    calendarDays.forEach((day) => {
      day.schedules.forEach((row) => {
        if (row.status === "EXECUTED") scheduleDone += 1;
        else schedulePending += 1;
      });
    });
    return {
      totalSpend,
      totalIncome,
      billCount: allLedgers.length,
      scheduleDone,
      schedulePending,
    };
  }, [calendarDays]);

  const calendarInsight = useMemo(() => {
    const peakSpendDay =
      [...calendarDays]
        .map((day) => ({
          ...day,
          expense_total: day.ledgers
            .filter((item) => getLedgerEntryKind(item) !== "income")
            .reduce((sum, item) => sum + Number(item.amount || 0), 0),
        }))
        .sort((a, b) => Number(b.expense_total || 0) - Number(a.expense_total || 0))[0] || null;
    const nextPending =
      calendarDays
        .flatMap((day) => day.schedules)
        .filter((item) => item.status !== "EXECUTED")
        .sort((a, b) => new Date(a.trigger_time).getTime() - new Date(b.trigger_time).getTime())[0] || null;

    return { peakSpendDay, nextPending };
  }, [calendarDays]);

  const listRows = useMemo(() => {
    const ledgers = calendarDays
      .flatMap((day) => day.ledgers.map((item) => ({ ...item, date: day.date })))
      .sort((a, b) => new Date(b.transaction_date).getTime() - new Date(a.transaction_date).getTime());
    const schedules = calendarDays
      .flatMap((day) => day.schedules.map((item) => ({ ...item, date: day.date })))
      .sort((a, b) => new Date(a.trigger_time).getTime() - new Date(b.trigger_time).getTime());

    return {
      ledgers: ledgers.slice(0, 12),
      pendingSchedules: schedules.filter((item) => item.status !== "EXECUTED").slice(0, 10),
      doneSchedules: schedules.filter((item) => item.status === "EXECUTED").slice(0, 8),
    };
  }, [calendarDays]);

  const toggleScheduleMutation = useMutation({
    mutationFn: async (item: CalendarScheduleItem) => {
      const nextStatus = item.status === "EXECUTED" ? "PENDING" : "EXECUTED";
      return updateSchedule(
        item.id,
        {
          status: nextStatus,
        },
        token!
      );
    },
    onSuccess: refreshAll,
    onError: (error: Error) => {
      Alert.alert("更新日程失败", error.message);
    },
  });

  function openLedgerEditor(item: CalendarLedgerItem) {
    setEditingLedger({
      ...item,
      created_at: item.transaction_date,
    });
  }

  function openScheduleEditor(item: CalendarScheduleItem) {
    setEditingSchedule({
      ...item,
      created_at: item.trigger_time,
    });
  }

  function handleToggleSchedule(item: CalendarScheduleItem) {
    if (toggleScheduleMutation.isPending) return;
    void toggleScheduleMutation.mutateAsync(item);
  }

  const tabBarWidth = pageWidth - spacing.pageX * 2;
  const tabIndicatorWidth = Math.max((tabBarWidth - 12) / 2, 0);
  const tabIndicatorTranslateX = scrollX.interpolate({
    inputRange: [0, pageWidth],
    outputRange: [0, tabIndicatorWidth],
    extrapolate: "clamp",
  });
  const calendarTextColor = scrollX.interpolate({
    inputRange: [0, pageWidth],
    outputRange: ["#ffffff", colors.text3],
    extrapolate: "clamp",
  });
  const listTextColor = scrollX.interpolate({
    inputRange: [0, pageWidth],
    outputRange: [colors.text3, "#ffffff"],
    extrapolate: "clamp",
  });
  const calendarParallax = scrollX.interpolate({
    inputRange: [0, pageWidth],
    outputRange: [0, -pageWidth * 0.08],
    extrapolate: "clamp",
  });
  const listParallax = scrollX.interpolate({
    inputRange: [0, pageWidth],
    outputRange: [pageWidth * 0.08, 0],
    extrapolate: "clamp",
  });

  function handleViewChange(nextView: "calendar" | "list") {
    setActiveView(nextView);
    pagerRef.current?.scrollTo({ x: nextView === "calendar" ? 0 : pageWidth, animated: true });
  }

  function handlePagerMomentumEnd(event: NativeSyntheticEvent<NativeScrollEvent>) {
    const x = event.nativeEvent.contentOffset.x;
    setActiveView(x >= pageWidth / 2 ? "list" : "calendar");
  }

  function renderCalendarPage() {
    return (
      <ScrollView
        style={styles.pageScroll}
        contentContainerStyle={[styles.pageContent, { paddingBottom: bottomInset + 122 }]}
        refreshControl={<RefreshControl refreshing={calendarQuery.isRefetching} onRefresh={() => void refreshAll()} />}
        showsVerticalScrollIndicator={false}
      >
        <Animated.View style={{ transform: [{ translateX: calendarParallax }] }}>
          <View style={styles.overviewCard}>
            <View style={styles.overviewGlow} />
            <View style={styles.overviewMain}>
              <Text style={styles.overviewLabel}>月度节奏</Text>
              <Text style={styles.overviewValue}>{monthStats.schedulePending} 个待执行提醒</Text>
              <Text style={styles.overviewMeta}>
                {calendarInsight.nextPending
                  ? `下一条：${calendarInsight.nextPending.content} · ${formatHmLocal(calendarInsight.nextPending.trigger_time)}`
                  : "近期没有待执行提醒"}
              </Text>
            </View>
            <View style={styles.overviewSide}>
              <Text style={styles.overviewSideLabel}>支出峰值日</Text>
              <Text style={styles.overviewSideValue}>
                {calendarInsight.peakSpendDay ? `¥${Number(calendarInsight.peakSpendDay.expense_total || 0).toFixed(0)}` : "—"}
              </Text>
              <Text style={styles.overviewSideMeta}>{calendarInsight.peakSpendDay?.date || "暂无"}</Text>
            </View>
          </View>

          <View style={styles.card}>
            <View style={styles.weekRow}>
              {WEEK_HEADERS.map((item) => (
                <View key={item} style={styles.weekCellWrap}>
                  <Text style={styles.weekCell}>{item}</Text>
                </View>
              ))}
            </View>
            <View style={styles.dayGrid}>
              {grid.map((cell) => (
                <View key={cell.key} style={styles.dayCellWrap}>
                  {cell.empty ? (
                    <View style={[styles.dayCell, styles.dayCellEmpty]} />
                  ) : (
                    <Pressable
                      style={[
                        styles.dayCell,
                        cell.date === activeDate && styles.dayCellActive,
                        cell.isToday && styles.dayCellToday,
                      ]}
                      onPress={() => setActiveDate(cell.date)}
                    >
                      <View style={styles.dayInner}>
                        <Text style={[styles.dayNum, cell.date === activeDate && styles.dayNumActive]}>{cell.day}</Text>
                        <View style={styles.dayBadges}>
                          {cell.ledger_count > 0 ? (
                            <View style={[styles.dayBadge, styles.dayBadgeGreen]}>
                              <Text style={styles.dayBadgeText}>{cell.ledger_count}</Text>
                            </View>
                          ) : null}
                          {cell.schedule_count > 0 ? (
                            <View style={[styles.dayBadge, styles.dayBadgeBlue]}>
                              <Text style={styles.dayBadgeText}>{cell.schedule_count}</Text>
                            </View>
                          ) : null}
                        </View>
                      </View>
                    </Pressable>
                  )}
                </View>
              ))}
            </View>
          </View>

          <View style={styles.statsRow}>
            <View style={[styles.statsCard, styles.statsCardWide]}>
              <Text style={styles.statsTitle}>本月支出</Text>
              <Text style={styles.statsValue}>¥{monthStats.totalSpend.toFixed(0)}</Text>
              <Text style={styles.statsSub}>收入 ¥{monthStats.totalIncome.toFixed(0)}</Text>
            </View>
            <View style={styles.statsCard}>
              <Text style={styles.statsTitle}>日程完成</Text>
              <Text style={styles.statsValue}>{monthStats.scheduleDone}</Text>
              <Text style={styles.statsSub}>待执行 {monthStats.schedulePending}</Text>
            </View>
          </View>

          <View style={styles.card}>
            <View style={styles.detailHeader}>
              <Text style={styles.detailTitle}>{activeDay.date || "选择日期"} 详情</Text>
              <View style={styles.detailAddBtns}>
                <Pressable style={[styles.detailAddBtn, styles.detailAddBtnGreen]} onPress={() => setCreateLedgerOpen(true)}>
                  <Text style={styles.detailAddBtnGreenText}>+ 账单</Text>
                </Pressable>
                <Pressable style={[styles.detailAddBtn, styles.detailAddBtnBlue]} onPress={() => setCreateScheduleOpen(true)}>
                  <Text style={styles.detailAddBtnBlueText}>+ 日程</Text>
                </Pressable>
              </View>
            </View>

            <View style={styles.detailStatsRow}>
              <View style={styles.detailStatsChip}>
                <Ionicons name="wallet-outline" size={14} color={colors.accent} />
                <Text style={styles.detailStatsText}>账单 {activeDay.ledger_count}</Text>
              </View>
              <View style={styles.detailStatsChip}>
                <Ionicons name="alarm-outline" size={14} color={colors.primary} />
                <Text style={styles.detailStatsText}>日程 {activeDay.schedule_count}</Text>
              </View>
              <View style={styles.detailStatsChip}>
                <Ionicons name="stats-chart-outline" size={14} color={colors.warning} />
                <Text style={styles.detailStatsText}>
                  支出 ¥{activeDay.ledgers
                    .filter((item) => getLedgerEntryKind(item) !== "income")
                    .reduce((sum, item) => sum + Number(item.amount || 0), 0)
                    .toFixed(0)}
                </Text>
              </View>
            </View>

            <View style={styles.detailSection}>
              <View style={styles.detailSectionHead}>
                <View style={[styles.detailDot, { backgroundColor: colors.accent }]} />
                <Text style={styles.detailSectionLabel}>账单</Text>
              </View>
              {activeDay.ledgers.length === 0 ? <Text style={styles.emptyText}>当天无账单</Text> : null}
              {activeDay.ledgers.map((item) => (
                <View key={item.id} style={styles.detailRow}>
                  <View style={styles.detailMain}>
                    <Text style={styles.detailContent}>{item.item || "未命名账单"}</Text>
                    <Text style={styles.detailMeta}>
                      {formatHmLocal(item.transaction_date)} · {getLedgerDisplayCategory(item.category) || "未分类"}
                    </Text>
                  </View>
                  <Text style={[styles.detailAmount, getLedgerEntryKind(item) === "income" && styles.detailIncomeAmount]}>
                    {getLedgerEntryKind(item) === "income" ? "+" : "-"}¥{Number(item.amount || 0).toFixed(0)}
                  </Text>
                  <Pressable style={styles.moreBtn} onPress={() => openLedgerEditor(item)}>
                    <Ionicons name="ellipsis-horizontal" size={18} color={colors.text3} />
                  </Pressable>
                </View>
              ))}
            </View>

            <View style={styles.detailSection}>
              <View style={styles.detailSectionHead}>
                <View style={[styles.detailDot, { backgroundColor: colors.primary }]} />
                <Text style={styles.detailSectionLabel}>日程</Text>
              </View>
              {activeDay.schedules.length === 0 ? <Text style={styles.emptyText}>当天无日程</Text> : null}
              {activeDay.schedules.map((item) => (
                <View key={item.id} style={styles.detailRow}>
                  <Pressable
                    style={[styles.scheduleCheck, item.status === "EXECUTED" && styles.scheduleCheckDone]}
                    onPress={() => handleToggleSchedule(item)}
                  >
                    {item.status === "EXECUTED" ? <Ionicons name="checkmark" size={14} color="#ffffff" /> : null}
                  </Pressable>
                  <View style={styles.detailMain}>
                    <Text style={[styles.detailContent, item.status === "EXECUTED" && styles.doneText]}>{item.content}</Text>
                    <Text style={styles.detailMeta}>
                      {formatHmLocal(item.trigger_time)} · {getScheduleStatusLabel(item.status)}
                    </Text>
                  </View>
                  <Pressable style={styles.moreBtn} onPress={() => openScheduleEditor(item)}>
                    <Ionicons name="ellipsis-horizontal" size={18} color={colors.text3} />
                  </Pressable>
                </View>
              ))}
            </View>
          </View>
        </Animated.View>
      </ScrollView>
    );
  }

  function renderListPage() {
    return (
      <ScrollView
        style={styles.pageScroll}
        contentContainerStyle={[styles.pageContent, { paddingBottom: bottomInset + 122 }]}
        refreshControl={<RefreshControl refreshing={calendarQuery.isRefetching} onRefresh={() => void refreshAll()} />}
        showsVerticalScrollIndicator={false}
      >
        <Animated.View style={{ transform: [{ translateX: listParallax }] }}>
          <View style={styles.listSummaryRow}>
            <View style={[styles.listSummaryCard, styles.listSummaryCardPrimary]}>
              <Text style={styles.listSummaryLabel}>待执行</Text>
              <Text style={styles.listSummaryValue}>{listRows.pendingSchedules.length}</Text>
              <Text style={styles.listSummaryMeta}>本月待处理提醒</Text>
            </View>
            <View style={styles.listSummaryCard}>
              <Text style={styles.listSummaryLabel}>已完成</Text>
              <Text style={styles.listSummaryValueDark}>{monthStats.scheduleDone}</Text>
              <Text style={styles.listSummaryMeta}>本月已执行</Text>
            </View>
          </View>

          <StaggerGroup active={activeView === "list"} index={0}>
            <View style={styles.card}>
              <View style={styles.listHead}>
                <Text style={styles.cardTitle}>待执行日程</Text>
                <Text style={styles.listMeta}>{listRows.pendingSchedules.length} 条</Text>
              </View>
              {listRows.pendingSchedules.length === 0 ? (
                <Text style={styles.listEmptyText}>本月没有待执行日程</Text>
              ) : (
                listRows.pendingSchedules.map((item, index) => (
                  <View key={item.id} style={[styles.timelineRow, index < listRows.pendingSchedules.length - 1 && styles.timelineRowBorder]}>
                    <Pressable style={styles.timelineCheck} onPress={() => handleToggleSchedule(item)}>
                      <View style={styles.timelineCheckInner} />
                    </Pressable>
                    <View style={styles.timelineContent}>
                      <Text style={styles.timelineTitle}>{item.content}</Text>
                      <Text style={styles.timelineMeta}>
                        {formatDateLabel(item.date)} · {formatHmLocal(item.trigger_time)}
                      </Text>
                    </View>
                    <Pressable style={styles.timelineAction} onPress={() => openScheduleEditor(item)}>
                      <Ionicons name="create-outline" size={16} color={colors.primary} />
                    </Pressable>
                  </View>
                ))
              )}
            </View>
          </StaggerGroup>

          <StaggerGroup active={activeView === "list"} index={1}>
            <View style={styles.card}>
              <View style={styles.listHead}>
                <Text style={styles.cardTitle}>已完成日程</Text>
                <Text style={styles.listMeta}>{listRows.doneSchedules.length} 条</Text>
              </View>
              {listRows.doneSchedules.length === 0 ? (
                <Text style={styles.listEmptyText}>还没有完成项</Text>
              ) : (
                listRows.doneSchedules.map((item, index) => (
                  <View key={item.id} style={[styles.timelineRow, index < listRows.doneSchedules.length - 1 && styles.timelineRowBorder]}>
                    <Pressable style={[styles.timelineCheck, styles.timelineCheckDone]} onPress={() => handleToggleSchedule(item)}>
                      <Ionicons name="checkmark" size={14} color="#fff" />
                    </Pressable>
                    <View style={styles.timelineContent}>
                      <Text style={[styles.timelineTitle, styles.doneText]}>{item.content}</Text>
                      <Text style={styles.timelineMeta}>
                        {formatDateLabel(item.date)} · {formatHmLocal(item.trigger_time)}
                      </Text>
                    </View>
                    <Pressable style={styles.timelineAction} onPress={() => openScheduleEditor(item)}>
                      <Ionicons name="ellipsis-horizontal" size={16} color={colors.primary} />
                    </Pressable>
                  </View>
                ))
              )}
            </View>
          </StaggerGroup>

          <StaggerGroup active={activeView === "list"} index={2}>
            <View style={styles.card}>
              <View style={styles.listHead}>
                <Text style={styles.cardTitle}>最近账单</Text>
                <Text style={styles.listMeta}>{listRows.ledgers.length} 笔</Text>
              </View>
              {listRows.ledgers.length === 0 ? (
                <Text style={styles.listEmptyText}>本月还没有账单记录</Text>
              ) : (
                listRows.ledgers.map((item, index) => (
                  <Pressable key={item.id} style={[styles.billRow, index < listRows.ledgers.length - 1 && styles.billRowBorder]} onPress={() => openLedgerEditor(item)}>
                    <View style={styles.billIconWrap}>
                      <Ionicons
                        name={getLedgerEntryKind(item) === "income" ? "arrow-down-outline" : "arrow-up-outline"}
                        size={16}
                        color={getLedgerEntryKind(item) === "income" ? colors.accent : colors.primary}
                      />
                    </View>
                    <View style={styles.billMain}>
                      <Text style={styles.billTitle}>{item.item || "未命名账单"}</Text>
                      <Text style={styles.billMeta}>
                        {formatDateLabel(item.date)} · {getLedgerDisplayCategory(item.category) || "未分类"}
                      </Text>
                    </View>
                    <Text style={[styles.billAmount, getLedgerEntryKind(item) === "income" && styles.detailIncomeAmount]}>
                      {getLedgerEntryKind(item) === "income" ? "+" : "-"}¥{Number(item.amount || 0).toFixed(0)}
                    </Text>
                  </Pressable>
                ))
              )}
            </View>
          </StaggerGroup>
        </Animated.View>
      </ScrollView>
    );
  }

  return (
    <>
      <View style={styles.page}>
        <View style={[styles.headerShell, { paddingTop: insets.top + 8 }]}>
          <View style={styles.toolbar}>
            <Pressable style={styles.navBtn} onPress={() => setAnchorMonth((prev) => addMonths(prev, -1))}>
              <Text style={styles.navBtnText}>‹ 上月</Text>
            </Pressable>
            <Text style={styles.monthLabel}>{formatMonthLabel(anchorMonth)}</Text>
            <Pressable style={styles.navBtn} onPress={() => setAnchorMonth((prev) => addMonths(prev, 1))}>
              <Text style={styles.navBtnText}>下月 ›</Text>
            </Pressable>
          </View>

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
            <Pressable style={styles.tabItem} onPress={() => handleViewChange("calendar")}>
              <Animated.Text style={[styles.tabText, { color: calendarTextColor }]}>月历</Animated.Text>
            </Pressable>
            <Pressable style={styles.tabItem} onPress={() => handleViewChange("list")}>
              <Animated.Text style={[styles.tabText, { color: listTextColor }]}>清单</Animated.Text>
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
          <View style={[styles.pagerPage, { width: pageWidth }]}>{renderCalendarPage()}</View>
          <View style={[styles.pagerPage, { width: pageWidth }]}>{renderListPage()}</View>
        </Animated.ScrollView>
      </View>

      <CreateLedgerModal
        visible={createLedgerOpen}
        token={token}
        onClose={() => setCreateLedgerOpen(false)}
        onCreated={refreshAll}
      />

      <CreateScheduleModal
        visible={createScheduleOpen}
        token={token}
        initialDate={activeDate}
        onClose={() => setCreateScheduleOpen(false)}
        onCreated={refreshAll}
      />

      <EditLedgerModal
        visible={!!editingLedger}
        token={token}
        ledger={editingLedger}
        onClose={() => setEditingLedger(null)}
        onChanged={refreshAll}
      />

      <EditScheduleModal
        visible={!!editingSchedule}
        token={token}
        schedule={editingSchedule}
        onClose={() => setEditingSchedule(null)}
        onChanged={refreshAll}
      />
    </>
  );
}

const styles = StyleSheet.create({
  page: {
    flex: 1,
    backgroundColor: colors.bg,
  },
  headerShell: {
    paddingHorizontal: spacing.pageX,
    paddingBottom: 12,
    gap: 12,
  },
  toolbar: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
    paddingHorizontal: 16,
    paddingVertical: 14,
    ...surfaceCard,
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
  pageContent: {
    paddingHorizontal: spacing.pageX,
    paddingTop: 4,
    gap: 14,
  },
  overviewCard: {
    overflow: "hidden",
    flexDirection: "row",
    gap: 10,
    padding: 18,
    borderRadius: radii.xl,
    backgroundColor: colors.text,
  },
  overviewGlow: {
    position: "absolute",
    right: -28,
    top: -20,
    width: 132,
    height: 132,
    borderRadius: 999,
    backgroundColor: "rgba(255,255,255,0.08)",
  },
  overviewMain: {
    flex: 1.15,
    gap: 6,
  },
  overviewLabel: {
    fontSize: 12,
    fontWeight: "700",
    color: "rgba(255,255,255,0.62)",
  },
  overviewValue: {
    fontSize: 22,
    fontWeight: "800",
    color: "#fff",
  },
  overviewMeta: {
    fontSize: 12,
    lineHeight: 18,
    color: "rgba(255,255,255,0.72)",
  },
  overviewSide: {
    flex: 0.9,
    padding: 14,
    borderRadius: radii.md,
    backgroundColor: "rgba(255,255,255,0.08)",
    gap: 5,
  },
  overviewSideLabel: {
    fontSize: 12,
    fontWeight: "700",
    color: "rgba(255,255,255,0.62)",
  },
  overviewSideValue: {
    fontSize: 20,
    fontWeight: "800",
    color: "#fff",
  },
  overviewSideMeta: {
    fontSize: 12,
    color: "rgba(255,255,255,0.7)",
  },
  navBtn: {
    flex: 1,
    height: 42,
    borderRadius: radii.md,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: colors.bg,
    borderWidth: 1,
    borderColor: colors.border,
  },
  navBtnText: {
    fontSize: 13,
    fontWeight: "700",
    color: colors.text2,
  },
  monthLabel: {
    flex: 1.4,
    textAlign: "center",
    fontSize: 20,
    fontWeight: "700",
    color: colors.text,
  },
  card: {
    padding: 18,
    gap: 12,
    ...surfaceCard,
  },
  weekRow: {
    flexDirection: "row",
  },
  weekCellWrap: {
    width: "14.2857%",
    paddingHorizontal: 3,
  },
  weekCell: {
    textAlign: "center",
    fontSize: 12,
    fontWeight: "700",
    color: colors.text3,
  },
  dayGrid: {
    flexDirection: "row",
    flexWrap: "wrap",
  },
  dayCellWrap: {
    width: "14.2857%",
    paddingHorizontal: 3,
    paddingVertical: 3,
  },
  dayCell: {
    width: "100%",
    minHeight: 58,
    borderRadius: radii.sm,
    paddingVertical: 6,
    backgroundColor: colors.bg,
    borderWidth: 1.5,
    borderColor: "transparent",
    alignItems: "center",
    justifyContent: "center",
  },
  dayCellEmpty: {
    backgroundColor: "transparent",
  },
  dayCellActive: {
    backgroundColor: colors.primaryLight,
    borderColor: "rgba(79,110,247,0.24)",
  },
  dayCellToday: {
    borderColor: colors.primary,
  },
  dayInner: {
    alignItems: "center",
    gap: 3,
  },
  dayNum: {
    fontSize: 13,
    fontWeight: "700",
    color: colors.text,
  },
  dayNumActive: {
    color: colors.primaryDark,
  },
  dayBadges: {
    flexDirection: "row",
    gap: 4,
    minHeight: 18,
  },
  dayBadge: {
    minWidth: 18,
    height: 18,
    borderRadius: 999,
    alignItems: "center",
    justifyContent: "center",
    paddingHorizontal: 4,
  },
  dayBadgeGreen: {
    backgroundColor: colors.accent,
  },
  dayBadgeBlue: {
    backgroundColor: colors.primary,
  },
  dayBadgeText: {
    fontSize: 10,
    fontWeight: "700",
    color: "#ffffff",
  },
  statsRow: {
    flexDirection: "row",
    gap: 10,
  },
  statsCard: {
    flex: 1,
    padding: 16,
    ...surfaceCard,
  },
  statsCardWide: {
    flex: 1.2,
  },
  statsTitle: {
    fontSize: 12,
    fontWeight: "700",
    color: colors.text3,
  },
  statsValue: {
    marginTop: 8,
    fontSize: 26,
    fontWeight: "800",
    color: colors.text,
  },
  statsSub: {
    marginTop: 4,
    fontSize: 12,
    color: colors.text3,
  },
  detailHeader: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    flexWrap: "wrap",
    gap: 10,
  },
  detailTitle: {
    fontSize: 18,
    fontWeight: "700",
    color: colors.text,
  },
  detailAddBtns: {
    flexDirection: "row",
    gap: 8,
  },
  detailAddBtn: {
    paddingHorizontal: 14,
    paddingVertical: 8,
    borderRadius: radii.full,
  },
  detailAddBtnGreen: {
    backgroundColor: colors.accentLight,
  },
  detailAddBtnBlue: {
    backgroundColor: colors.primaryLight,
  },
  detailAddBtnGreenText: {
    fontSize: 12,
    fontWeight: "700",
    color: "#166534",
  },
  detailAddBtnBlueText: {
    fontSize: 12,
    fontWeight: "700",
    color: "#1e40af",
  },
  detailStatsRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8,
  },
  detailStatsChip: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    paddingHorizontal: 12,
    paddingVertical: 8,
    borderRadius: radii.full,
    backgroundColor: colors.bg,
  },
  detailStatsText: {
    fontSize: 12,
    fontWeight: "700",
    color: colors.text2,
  },
  detailSection: {
    gap: 8,
  },
  detailSectionHead: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
  },
  detailDot: {
    width: 10,
    height: 10,
    borderRadius: 999,
  },
  detailSectionLabel: {
    fontSize: 15,
    fontWeight: "700",
    color: colors.text,
  },
  emptyText: {
    fontSize: 13,
    color: colors.text3,
    paddingLeft: 18,
  },
  detailRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
    backgroundColor: colors.bg,
    borderRadius: radii.md,
    paddingHorizontal: 14,
    paddingVertical: 12,
  },
  detailMain: {
    flex: 1,
  },
  detailContent: {
    fontSize: 14,
    fontWeight: "700",
    color: colors.text,
  },
  detailMeta: {
    marginTop: 4,
    fontSize: 12,
    color: colors.text3,
  },
  detailAmount: {
    fontSize: 14,
    fontWeight: "800",
    color: colors.primary,
  },
  detailIncomeAmount: {
    color: colors.accent,
  },
  moreBtn: {
    width: 36,
    height: 36,
    borderRadius: 18,
    alignItems: "center",
    justifyContent: "center",
  },
  scheduleCheck: {
    width: 22,
    height: 22,
    borderRadius: 999,
    borderWidth: 2,
    borderColor: colors.border,
    alignItems: "center",
    justifyContent: "center",
  },
  scheduleCheckDone: {
    backgroundColor: colors.accent,
    borderColor: colors.accent,
  },
  doneText: {
    textDecorationLine: "line-through",
    color: colors.text3,
  },
  listSummaryRow: {
    flexDirection: "row",
    gap: 10,
  },
  listSummaryCard: {
    flex: 1,
    padding: 16,
    borderRadius: radii.lg,
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.borderLight,
    ...shadowSm,
  },
  listSummaryCardPrimary: {
    backgroundColor: colors.primary,
    borderColor: "rgba(79,110,247,0.18)",
  },
  listSummaryLabel: {
    fontSize: 12,
    fontWeight: "700",
    color: colors.text3,
  },
  listSummaryValue: {
    marginTop: 8,
    fontSize: 28,
    fontWeight: "800",
    color: "#fff",
  },
  listSummaryValueDark: {
    marginTop: 8,
    fontSize: 28,
    fontWeight: "800",
    color: colors.text,
  },
  listSummaryMeta: {
    marginTop: 4,
    fontSize: 12,
    color: colors.text3,
  },
  listHead: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
  },
  cardTitle: {
    fontSize: 18,
    fontWeight: "700",
    color: colors.text,
  },
  listMeta: {
    fontSize: 12,
    fontWeight: "700",
    color: colors.text3,
  },
  listEmptyText: {
    fontSize: 13,
    color: colors.text3,
    paddingVertical: 10,
  },
  timelineRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    paddingVertical: 12,
  },
  timelineRowBorder: {
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: colors.borderLight,
  },
  timelineCheck: {
    width: 28,
    height: 28,
    borderRadius: 14,
    alignItems: "center",
    justifyContent: "center",
    borderWidth: 1.5,
    borderColor: colors.border,
  },
  timelineCheckInner: {
    width: 10,
    height: 10,
    borderRadius: 999,
    backgroundColor: colors.primary,
  },
  timelineCheckDone: {
    backgroundColor: colors.accent,
    borderColor: colors.accent,
  },
  timelineContent: {
    flex: 1,
    gap: 4,
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
  billRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    paddingVertical: 12,
  },
  billRowBorder: {
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: colors.borderLight,
  },
  billIconWrap: {
    width: 34,
    height: 34,
    borderRadius: 17,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: colors.bg,
  },
  billMain: {
    flex: 1,
    gap: 4,
  },
  billTitle: {
    fontSize: 14,
    fontWeight: "700",
    color: colors.text,
  },
  billMeta: {
    fontSize: 12,
    color: colors.text3,
  },
  billAmount: {
    fontSize: 14,
    fontWeight: "800",
    color: colors.primary,
  },
});
