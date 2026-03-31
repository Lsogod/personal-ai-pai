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
import { deleteLedger, LedgerItem, updateLedger } from "../../lib/api";
import { colors, radii, surfaceCard } from "../../design/tokens";

type EditLedgerModalProps = {
  visible: boolean;
  token: string | null;
  ledger: LedgerItem | null;
  onClose: () => void;
  onChanged: () => Promise<void> | void;
};

export function EditLedgerModal({ visible, token, ledger, onClose, onChanged }: EditLedgerModalProps) {
  const [amountText, setAmountText] = useState("");
  const [category, setCategory] = useState("");
  const [item, setItem] = useState("");
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    if (!visible || !ledger) return;
    setAmountText(String(Number(ledger.amount || 0)));
    setCategory(ledger.category || "");
    setItem(ledger.item || "");
    setNotice(null);
  }, [ledger, visible]);

  const amount = useMemo(() => Number(amountText.replace(/,/g, ".").trim()), [amountText]);

  const saveMutation = useMutation({
    mutationFn: () =>
      updateLedger(
        ledger!.id,
        {
          amount,
          category: category.trim() || "",
          item: item.trim() || "",
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
    mutationFn: () => deleteLedger(ledger!.id, token!),
    onSuccess: async () => {
      await onChanged();
      onClose();
    },
    onError: (error: Error) => setNotice(error.message),
  });

  const busy = saveMutation.isPending || deleteMutation.isPending;
  const canSubmit = !!ledger && Number.isFinite(amount) && amount >= 0 && !busy;

  function handleDelete() {
    if (!ledger || busy) return;
    Alert.alert("删除账单", `确认删除“${ledger.item || "未命名账单"}”吗？`, [
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
    <PanelModal visible={visible} title="编辑账单" onClose={onClose}>
      <View style={styles.hero}>
        <Text style={styles.heroTitle}>修改账单</Text>
        <Text style={styles.heroDesc}>直接调用后端 `PATCH /api/ledgers/{'{id}'}` 和 `DELETE /api/ledgers/{'{id}'}`。</Text>
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
          {deleteMutation.isPending ? <ActivityIndicator color={colors.danger} /> : <Text style={styles.deleteBtnText}>删除账单</Text>}
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
