import { useEffect, useMemo, useState } from "react";
import {
  ActivityIndicator,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { useMutation } from "@tanstack/react-query";

import { PanelModal } from "../../components/PanelModal";
import { createSchedule } from "../../lib/api";
import { formatYmd, pad2 } from "../../lib/date";
import { colors, radii, surfaceCard } from "../../design/tokens";

type CreateScheduleModalProps = {
  visible: boolean;
  token: string | null;
  initialDate?: string;
  onClose: () => void;
  onCreated: () => Promise<void> | void;
};

function getDefaultDraft(initialDate?: string) {
  if (initialDate) {
    return { dateText: initialDate.slice(0, 10), timeText: "09:00" };
  }
  const nextHour = new Date();
  nextHour.setMinutes(0, 0, 0);
  nextHour.setHours(nextHour.getHours() + 1);
  return {
    dateText: formatYmd(nextHour),
    timeText: `${pad2(nextHour.getHours())}:${pad2(nextHour.getMinutes())}`,
  };
}

export function CreateScheduleModal({
  visible,
  token,
  initialDate,
  onClose,
  onCreated,
}: CreateScheduleModalProps) {
  const [content, setContent] = useState("");
  const [dateText, setDateText] = useState("");
  const [timeText, setTimeText] = useState("");
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    if (!visible) return;
    const draft = getDefaultDraft(initialDate);
    setContent("");
    setDateText(draft.dateText);
    setTimeText(draft.timeText);
    setNotice(null);
  }, [initialDate, visible]);

  const isDateValid = /^\d{4}-\d{2}-\d{2}$/.test(dateText.trim());
  const isTimeValid = /^\d{2}:\d{2}$/.test(timeText.trim());
  const triggerTime = useMemo(() => `${dateText.trim()}T${timeText.trim()}:00`, [dateText, timeText]);

  const createMutation = useMutation({
    mutationFn: () =>
      createSchedule(
        {
          content: content.trim(),
          trigger_time: triggerTime,
        },
        token!
      ),
    onSuccess: async () => {
      await onCreated();
      onClose();
    },
    onError: (error: Error) => setNotice(error.message),
  });

  const canSubmit =
    content.trim().length > 0 && isDateValid && isTimeValid && !createMutation.isPending;

  return (
    <PanelModal visible={visible} title="新建日程" onClose={onClose}>
      <View style={styles.hero}>
        <Text style={styles.heroTitle}>添加提醒</Text>
        <Text style={styles.heroDesc}>直接调用后端 `POST /api/schedules`，保存后会刷新当前月历和当天详情。</Text>
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

        <Text style={styles.hint}>当前会按本地时间发送给后端，格式为 `YYYY-MM-DD` 和 `HH:MM`。</Text>
        {notice ? <Text style={styles.notice}>{notice}</Text> : null}

        <Pressable
          style={[styles.primaryBtn, !canSubmit && styles.primaryBtnDisabled]}
          disabled={!canSubmit}
          onPress={() => void createMutation.mutateAsync()}
        >
          {createMutation.isPending ? (
            <ActivityIndicator color="#ffffff" />
          ) : (
            <Text style={styles.primaryBtnText}>保存日程</Text>
          )}
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
  hint: {
    fontSize: 12,
    lineHeight: 18,
    color: colors.text3,
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
});
