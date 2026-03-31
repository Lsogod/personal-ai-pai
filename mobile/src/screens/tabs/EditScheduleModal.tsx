import { useEffect, useMemo, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { useMutation } from "@tanstack/react-query";

import { PanelModal } from "../../components/PanelModal";
import { deleteSchedule, ScheduleItem, updateSchedule } from "../../lib/api";
import { formatYmd, pad2, parseServerDate } from "../../lib/date";
import { colors, radii, surfaceCard } from "../../design/tokens";

type EditScheduleModalProps = {
  visible: boolean;
  token: string | null;
  schedule: ScheduleItem | null;
  onClose: () => void;
  onChanged: () => Promise<void> | void;
};

const STATUS_OPTIONS = [
  { value: "PENDING", label: "待执行" },
  { value: "EXECUTED", label: "已完成" },
  { value: "CANCELLED", label: "已取消" },
];

function getDraft(schedule: ScheduleItem | null) {
  if (!schedule) {
    return { content: "", dateText: "", timeText: "", status: "PENDING" };
  }
  const date = parseServerDate(schedule.trigger_time);
  return {
    content: schedule.content || "",
    dateText: formatYmd(date),
    timeText: `${pad2(date.getHours())}:${pad2(date.getMinutes())}`,
    status: schedule.status || "PENDING",
  };
}

export function EditScheduleModal({ visible, token, schedule, onClose, onChanged }: EditScheduleModalProps) {
  const [content, setContent] = useState("");
  const [dateText, setDateText] = useState("");
  const [timeText, setTimeText] = useState("");
  const [status, setStatus] = useState("PENDING");
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    if (!visible) return;
    const draft = getDraft(schedule);
    setContent(draft.content);
    setDateText(draft.dateText);
    setTimeText(draft.timeText);
    setStatus(draft.status);
    setNotice(null);
  }, [schedule, visible]);

  const isDateValid = /^\d{4}-\d{2}-\d{2}$/.test(dateText.trim());
  const isTimeValid = /^\d{2}:\d{2}$/.test(timeText.trim());
  const triggerTime = useMemo(() => `${dateText.trim()}T${timeText.trim()}:00`, [dateText, timeText]);

  const saveMutation = useMutation({
    mutationFn: () =>
      updateSchedule(
        schedule!.id,
        {
          content: content.trim(),
          trigger_time: triggerTime,
          status,
        },
        token!
      ),
    onSuccess: async () => {
      await onChanged();
      onClose();
    },
    onError: (error: Error) => setNotice(error.message),
  });

  const deleteMutation = useMutation({
    mutationFn: () => deleteSchedule(schedule!.id, token!),
    onSuccess: async () => {
      await onChanged();
      onClose();
    },
    onError: (error: Error) => setNotice(error.message),
  });

  const busy = saveMutation.isPending || deleteMutation.isPending;
  const canSubmit = !!schedule && content.trim().length > 0 && isDateValid && isTimeValid && !busy;

  function handleDelete() {
    if (!schedule || busy) return;
    Alert.alert("删除日程", `确认删除“${schedule.content}”吗？`, [
      { text: "取消", style: "cancel" },
      {
        text: "删除",
        style: "destructive",
        onPress: () => {
          void deleteMutation.mutateAsync();
        },
      },
    ]);
  }

  return (
    <PanelModal visible={visible} title="编辑日程" onClose={onClose}>
      <View style={styles.hero}>
        <Text style={styles.heroTitle}>修改提醒</Text>
        <Text style={styles.heroDesc}>直接调用后端 `PATCH /api/schedules/{'{id}'}` 和 `DELETE /api/schedules/{'{id}'}`。</Text>
      </View>

      <View style={styles.card}>
        <Text style={styles.label}>提醒内容</Text>
        <TextInput
          value={content}
          onChangeText={(value) => {
            setContent(value);
            setNotice(null);
          }}
          placeholder="例如 明早开会 / 交房租"
          placeholderTextColor={colors.text4}
          style={styles.input}
        />

        <View style={styles.row}>
          <View style={styles.field}>
            <Text style={styles.label}>日期</Text>
            <TextInput
              value={dateText}
              onChangeText={(value) => {
                setDateText(value);
                setNotice(null);
              }}
              placeholder="YYYY-MM-DD"
              placeholderTextColor={colors.text4}
              style={styles.input}
            />
          </View>
          <View style={styles.field}>
            <Text style={styles.label}>时间</Text>
            <TextInput
              value={timeText}
              onChangeText={(value) => {
                setTimeText(value);
                setNotice(null);
              }}
              placeholder="09:00"
              placeholderTextColor={colors.text4}
              style={styles.input}
            />
          </View>
        </View>

        <Text style={styles.label}>状态</Text>
        <View style={styles.statusRow}>
          {STATUS_OPTIONS.map((option) => {
            const active = status === option.value;
            return (
              <Pressable
                key={option.value}
                style={[styles.statusChip, active && styles.statusChipActive]}
                onPress={() => {
                  setStatus(option.value);
                  setNotice(null);
                }}
              >
                <Text style={[styles.statusChipText, active && styles.statusChipTextActive]}>{option.label}</Text>
              </Pressable>
            );
          })}
        </View>

        {notice ? <Text style={styles.notice}>{notice}</Text> : null}

        <Pressable
          style={[styles.primaryBtn, !canSubmit && styles.primaryBtnDisabled]}
          disabled={!canSubmit}
          onPress={() => void saveMutation.mutateAsync()}
        >
          {saveMutation.isPending ? <ActivityIndicator color="#ffffff" /> : <Text style={styles.primaryBtnText}>保存修改</Text>}
        </Pressable>

        <Pressable
          style={[styles.deleteBtn, busy && styles.primaryBtnDisabled]}
          disabled={busy}
          onPress={handleDelete}
        >
          {deleteMutation.isPending ? <ActivityIndicator color={colors.danger} /> : <Text style={styles.deleteBtnText}>删除日程</Text>}
        </Pressable>
      </View>
    </PanelModal>
  );
}

const styles = StyleSheet.create({
  hero: {
    padding: 18,
    borderRadius: radii.lg,
    backgroundColor: colors.primary,
    gap: 6,
  },
  heroTitle: {
    fontSize: 18,
    fontWeight: "800",
    color: "#ffffff",
  },
  heroDesc: {
    fontSize: 13,
    lineHeight: 19,
    color: "rgba(255,255,255,0.86)",
  },
  card: {
    gap: 12,
    padding: 18,
    ...surfaceCard,
  },
  row: {
    flexDirection: "row",
    gap: 10,
  },
  field: {
    flex: 1,
    gap: 8,
  },
  label: {
    fontSize: 13,
    fontWeight: "700",
    color: colors.text2,
  },
  input: {
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radii.md,
    backgroundColor: colors.bg,
    paddingHorizontal: 14,
    paddingVertical: 14,
    fontSize: 16,
    color: colors.text,
  },
  statusRow: {
    flexDirection: "row",
    gap: 10,
  },
  statusChip: {
    flex: 1,
    minHeight: 44,
    borderRadius: radii.md,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: colors.bg,
    borderWidth: 1,
    borderColor: colors.border,
  },
  statusChipActive: {
    backgroundColor: colors.primaryLight,
    borderColor: "rgba(79,110,247,0.3)",
  },
  statusChipText: {
    fontSize: 14,
    fontWeight: "700",
    color: colors.text2,
  },
  statusChipTextActive: {
    color: colors.primaryDark,
  },
  notice: {
    borderRadius: radii.md,
    backgroundColor: colors.dangerLight,
    color: colors.danger,
    paddingHorizontal: 14,
    paddingVertical: 12,
    fontSize: 13,
    lineHeight: 19,
  },
  primaryBtn: {
    alignItems: "center",
    justifyContent: "center",
    minHeight: 48,
    borderRadius: radii.md,
    backgroundColor: colors.primary,
  },
  primaryBtnDisabled: {
    opacity: 0.55,
  },
  primaryBtnText: {
    fontSize: 15,
    fontWeight: "700",
    color: "#ffffff",
  },
  deleteBtn: {
    alignItems: "center",
    justifyContent: "center",
    minHeight: 48,
    borderRadius: radii.md,
    backgroundColor: colors.dangerLight,
    borderWidth: 1,
    borderColor: "rgba(239,68,68,0.18)",
  },
  deleteBtnText: {
    fontSize: 15,
    fontWeight: "700",
    color: colors.danger,
  },
});
