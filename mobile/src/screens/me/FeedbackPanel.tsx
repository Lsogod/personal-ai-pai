import { useState } from "react";
import {
  ActivityIndicator,
  Platform,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { useMutation, useQuery } from "@tanstack/react-query";

import { PanelModal } from "../../components/PanelModal";
import { fetchProfile, submitUserFeedback } from "../../lib/api";
import { colors, radii, surfaceCard } from "../../design/tokens";

type FeedbackPanelProps = {
  visible: boolean;
  token: string | null;
  onClose: () => void;
};

export function FeedbackPanel({ visible, token, onClose }: FeedbackPanelProps) {
  const [content, setContent] = useState("");
  const [notice, setNotice] = useState<string | null>(null);

  const profileQuery = useQuery({
    queryKey: ["profile"],
    enabled: visible && !!token,
    queryFn: () => fetchProfile(token!),
  });

  const submitMutation = useMutation({
    mutationFn: () =>
      submitUserFeedback(
        {
          content: content.trim(),
          app_version: "expo-dev",
          env_version: `${Platform.OS}-${String(Platform.Version)}`,
          client_page: "mobile/me/feedback",
        },
        token!
      ),
    onSuccess: () => {
      setContent("");
      setNotice("反馈已提交，后端已经收到这条建议。");
    },
    onError: (error: Error) => setNotice(error.message),
  });

  return (
    <PanelModal visible={visible} title="问题反馈" onClose={onClose}>
      <View style={styles.hero}>
        <Text style={styles.heroTitle}>把原生端问题直接提上来</Text>
        <Text style={styles.heroDesc}>这里沿用小程序同一条反馈接口，便于统一收集 UI、交互和功能问题。</Text>
      </View>

      {profileQuery.isLoading ? (
        <View style={styles.loadingBox}>
          <ActivityIndicator color={colors.primary} />
          <Text style={styles.loadingText}>正在读取账号信息...</Text>
        </View>
      ) : (
        <View style={styles.card}>
          <Text style={styles.cardTitle}>反馈账号</Text>
          <Text style={styles.cardSub}>{profileQuery.data?.nickname || "用户"}</Text>
          {profileQuery.data?.email ? <Text style={styles.cardSub}>{profileQuery.data.email}</Text> : null}
        </View>
      )}

      <View style={styles.card}>
        <Text style={styles.cardTitle}>反馈内容</Text>
        <TextInput
          value={content}
          onChangeText={(value) => {
            setContent(value);
            setNotice(null);
          }}
          multiline
          textAlignVertical="top"
          placeholder="例如：iPhone 16 Pro Max 顶部安全区过高，账单页需要补新增入口..."
          placeholderTextColor={colors.text4}
          style={styles.textarea}
        />
        <Text style={styles.countText}>{content.trim().length} / 2000</Text>
        {notice ? <Text style={styles.notice}>{notice}</Text> : null}
        <Pressable
          style={[styles.primaryBtn, (content.trim().length < 4 || submitMutation.isPending) && styles.primaryBtnDisabled]}
          disabled={content.trim().length < 4 || submitMutation.isPending}
          onPress={() => void submitMutation.mutateAsync()}
        >
          <Text style={styles.primaryBtnText}>{submitMutation.isPending ? "提交中..." : "提交反馈"}</Text>
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
  loadingBox: {
    alignItems: "center",
    gap: 10,
    paddingVertical: 28,
    ...surfaceCard,
  },
  loadingText: {
    fontSize: 14,
    color: colors.text3,
  },
  card: {
    gap: 12,
    padding: 18,
    ...surfaceCard,
  },
  cardTitle: {
    fontSize: 17,
    fontWeight: "800",
    color: colors.text,
  },
  cardSub: {
    fontSize: 13,
    lineHeight: 19,
    color: colors.text3,
  },
  textarea: {
    minHeight: 160,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radii.md,
    backgroundColor: colors.bg,
    paddingHorizontal: 14,
    paddingVertical: 14,
    fontSize: 15,
    lineHeight: 22,
    color: colors.text,
  },
  countText: {
    textAlign: "right",
    fontSize: 12,
    color: colors.text4,
  },
  notice: {
    borderRadius: radii.md,
    backgroundColor: colors.primaryLight,
    color: colors.primaryDark,
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
