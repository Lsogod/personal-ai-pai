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
import { createLedger } from "../../lib/api";
import { colors, radii, surfaceCard } from "../../design/tokens";

type CreateLedgerModalProps = {
  visible: boolean;
  token: string | null;
  onClose: () => void;
  onCreated: () => Promise<void> | void;
};

export function CreateLedgerModal({
  visible,
  token,
  onClose,
  onCreated,
}: CreateLedgerModalProps) {
  const [amountText, setAmountText] = useState("");
  const [category, setCategory] = useState("");
  const [item, setItem] = useState("");
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    if (!visible) return;
    setAmountText("");
    setCategory("");
    setItem("");
    setNotice(null);
  }, [visible]);

  const amount = useMemo(() => Number(amountText.replace(/,/g, ".").trim()), [amountText]);

  const createMutation = useMutation({
    mutationFn: () =>
      createLedger(
        {
          amount,
          category: category.trim() || undefined,
          item: item.trim() || undefined,
        },
        token!
      ),
    onSuccess: async () => {
      await onCreated();
      onClose();
    },
    onError: (error: Error) => setNotice(error.message),
  });

  const canSubmit = Number.isFinite(amount) && amount > 0 && !createMutation.isPending;

  return (
    <PanelModal visible={visible} title="新建账单" onClose={onClose}>
      <View style={styles.hero}>
        <Text style={styles.heroTitle}>手动记一笔</Text>
        <Text style={styles.heroDesc}>直接对齐后端 `POST /api/ledgers`，保存后会刷新账单明细和本月统计。</Text>
      </View>

      <View style={styles.card}>
        <Text style={styles.label}>金额</Text>
        <TextInput
          value={amountText}
          onChangeText={(value) => {
            setAmountText(value);
            setNotice(null);
          }}
          keyboardType="decimal-pad"
          placeholder="例如 35.5"
          placeholderTextColor={colors.text4}
          style={styles.input}
        />

        <Text style={styles.label}>分类</Text>
        <TextInput
          value={category}
          onChangeText={(value) => {
            setCategory(value);
            setNotice(null);
          }}
          placeholder="例如 餐饮 / 交通"
          placeholderTextColor={colors.text4}
          style={styles.input}
        />

        <Text style={styles.label}>项目</Text>
        <TextInput
          value={item}
          onChangeText={(value) => {
            setItem(value);
            setNotice(null);
          }}
          placeholder="例如 午饭 / 地铁"
          placeholderTextColor={colors.text4}
          style={styles.input}
        />

        <Text style={styles.hint}>时间默认使用当前时间；分类和项目为空时由后端填默认值。</Text>
        {notice ? <Text style={styles.notice}>{notice}</Text> : null}

        <Pressable
          style={[styles.primaryBtn, !canSubmit && styles.primaryBtnDisabled]}
          disabled={!canSubmit}
          onPress={() => void createMutation.mutateAsync()}
        >
          {createMutation.isPending ? (
            <ActivityIndicator color="#ffffff" />
          ) : (
            <Text style={styles.primaryBtnText}>保存账单</Text>
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
