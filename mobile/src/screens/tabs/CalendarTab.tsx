import { Ionicons } from "@expo/vector-icons";
import { useEffect, useMemo, useState } from "react";
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

import { CalendarDay, fetchCalendar } from "../../lib/api";
import {
  addMonths,
  buildCalendarGrid,
  formatHmLocal,
  formatMonthLabel,
  getMonthRange,
  getScheduleStatusLabel,
} from "../../lib/date";
import { useAuthStore } from "../../store/auth";
import { colors, radii, spacing, surfaceCard } from "../../design/tokens";

type CalendarTabProps = {
  bottomInset: number;
};

const WEEK_HEADERS = ["一", "二", "三", "四", "五", "六", "日"];

export function CalendarTab({ bottomInset }: CalendarTabProps) {
  const token = useAuthStore((state) => state.token);
  const queryClient = useQueryClient();
  const insets = useSafeAreaInsets();
  const [anchorMonth, setAnchorMonth] = useState(() => new Date(new Date().getFullYear(), new Date().getMonth(), 1));
  const [activeDate, setActiveDate] = useState("");

  const range = useMemo(() => getMonthRange(anchorMonth), [anchorMonth]);

  const calendarQuery = useQuery({
    queryKey: ["calendar", range.startText, range.endText],
    enabled: !!token,
    queryFn: () => fetchCalendar(token!, range.startText, range.endText),
  });

  useEffect(() => {
    const today = new Date();
    if (today.getFullYear() === anchorMonth.getFullYear() && today.getMonth() === anchorMonth.getMonth()) {
      setActiveDate(`${today.getFullYear()}-${`${today.getMonth() + 1}`.padStart(2, "0")}-${`${today.getDate()}`.padStart(2, "0")}`);
      return;
    }
    setActiveDate(range.startText);
  }, [anchorMonth, range.startText]);

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
    const totalSpend = calendarDays.reduce((sum, item) => sum + Number(item.ledger_total || 0), 0);
    const billCount = calendarDays.reduce((sum, item) => sum + Number(item.ledger_count || 0), 0);
    let scheduleDone = 0;
    let schedulePending = 0;
    calendarDays.forEach((day) => {
      day.schedules.forEach((row) => {
        if (row.status === "EXECUTED") scheduleDone += 1;
        else schedulePending += 1;
      });
    });
    return { totalSpend, billCount, scheduleDone, schedulePending };
  }, [calendarDays]);

  async function refreshAll() {
    await queryClient.invalidateQueries({ queryKey: ["calendar", range.startText, range.endText] });
  }

  return (
    <ScrollView
      style={styles.page}
      contentContainerStyle={{ paddingBottom: bottomInset + 20 }}
      refreshControl={<RefreshControl refreshing={calendarQuery.isRefetching} onRefresh={() => void refreshAll()} />}
      showsVerticalScrollIndicator={false}
    >
      <View style={[styles.inner, { paddingTop: insets.top + 8 }]}>
        <View style={styles.toolbar}>
          <Pressable style={styles.navBtn} onPress={() => setAnchorMonth((prev) => addMonths(prev, -1))}>
            <Text style={styles.navBtnText}>‹ 上月</Text>
          </Pressable>
          <Text style={styles.monthLabel}>{formatMonthLabel(anchorMonth)}</Text>
          <Pressable style={styles.navBtn} onPress={() => setAnchorMonth((prev) => addMonths(prev, 1))}>
            <Text style={styles.navBtnText}>下月 ›</Text>
          </Pressable>
        </View>

        <View style={styles.card}>
          <View style={styles.weekRow}>
            {WEEK_HEADERS.map((item) => (
              <Text key={item} style={styles.weekCell}>
                {item}
              </Text>
            ))}
          </View>
          <View style={styles.dayGrid}>
            {grid.map((cell) =>
              cell.empty ? (
                <View key={cell.key} style={[styles.dayCell, styles.dayCellEmpty]} />
              ) : (
                <Pressable
                  key={cell.key}
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
                      {cell.ledger_count > 0 ? <View style={[styles.dayBadge, styles.dayBadgeGreen]}><Text style={styles.dayBadgeText}>{cell.ledger_count}</Text></View> : null}
                      {cell.schedule_count > 0 ? <View style={[styles.dayBadge, styles.dayBadgeBlue]}><Text style={styles.dayBadgeText}>{cell.schedule_count}</Text></View> : null}
                    </View>
                  </View>
                </Pressable>
              )
            )}
          </View>
        </View>

        <View style={styles.statsRow}>
          <View style={[styles.statsCard, styles.statsCardWide]}>
            <Text style={styles.statsTitle}>本月支出</Text>
            <Text style={styles.statsValue}>¥{monthStats.totalSpend.toFixed(0)}</Text>
            <Text style={styles.statsSub}>共 {monthStats.billCount} 笔</Text>
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
                    {formatHmLocal(item.transaction_date)} · {item.category || "未分类"}
                  </Text>
                </View>
                <Text style={styles.detailAmount}>¥{Number(item.amount || 0).toFixed(0)}</Text>
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
                <View style={[styles.scheduleCheck, item.status === "EXECUTED" && styles.scheduleCheckDone]}>
                  {item.status === "EXECUTED" ? <Ionicons name="checkmark" size={14} color="#ffffff" /> : null}
                </View>
                <View style={styles.detailMain}>
                  <Text style={[styles.detailContent, item.status === "EXECUTED" && styles.doneText]}>{item.content}</Text>
                  <Text style={styles.detailMeta}>
                    {formatHmLocal(item.trigger_time)} · {getScheduleStatusLabel(item.status)}
                  </Text>
                </View>
              </View>
            ))}
          </View>
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
  inner: {
    paddingHorizontal: spacing.pageX,
    gap: 16,
  },
  toolbar: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
    paddingHorizontal: 16,
    paddingVertical: 14,
    ...surfaceCard,
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
    justifyContent: "space-between",
  },
  weekCell: {
    width: `${100 / 7}%`,
    textAlign: "center",
    fontSize: 12,
    fontWeight: "700",
    color: colors.text3,
  },
  dayGrid: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 6,
  },
  dayCell: {
    width: "13.5%",
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
  },
  detailTitle: {
    fontSize: 18,
    fontWeight: "700",
    color: colors.text,
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
});
