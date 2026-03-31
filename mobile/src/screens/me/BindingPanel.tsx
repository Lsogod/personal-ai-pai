import { useMemo, useState } from "react";
import {
  ActivityIndicator,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { PanelModal } from "../../components/PanelModal";
import {
  consumeBindCode,
  createBindCode,
  fetchIdentities,
  fetchProfile,
  getSourcePlatformLabel,
} from "../../lib/api";
import { formatMdHmLocal } from "../../lib/date";
import { colors, radii, surfaceCard } from "../../design/tokens";
import { useAuthStore } from "../../store/auth";

type BindingPanelProps = {
  visible: boolean;
  token: string | null;
  onClose: () => void;
};

function platformLabel(platform: string) {
  const key = String(platform || "").toLowerCase();
  if (key === "web") return "邮箱 / Web";
  if (key === "miniapp") return "微信小程序";
  if (key === "ios") return "iOS App";
  if (key === "android") return "Android App";
  if (key === "app") return "移动 App";
  return getSourcePlatformLabel(platform);
}

function platformValue(platform: string, platformId: string) {
  const key = String(platform || "").toLowerCase();
  const raw = String(platformId || "").trim();
  if (key === "ios") return "当前 iPhone / iPad";
  if (key === "android") return "当前 Android 设备";
  if (key === "app") return "当前移动端";
  if (raw.startsWith("web:")) return "当前 Web 会话";
  return raw || "已绑定";
}

export function BindingPanel({ visible, token, onClose }: BindingPanelProps) {
  const setToken = useAuthStore((state) => state.setToken);
  const queryClient = useQueryClient();
  const [bindCode, setBindCode] = useState("");
  const [notice, setNotice] = useState<string | null>(null);

  const profileQuery = useQuery({
    queryKey: ["profile"],
    enabled: visible && !!token,
    queryFn: () => fetchProfile(token!),
  });

  const identitiesQuery = useQuery({
    queryKey: ["identities"],
    enabled: visible && !!token,
    queryFn: () => fetchIdentities(token!),
  });

  const createMutation = useMutation({
    mutationFn: () => createBindCode(10, token!),
    onSuccess: (data) => {
      setNotice(`绑定码已生成：${data.code}，有效期至 ${formatMdHmLocal(data.expires_at)}`);
    },
    onError: (error: Error) => setNotice(error.message),
  });

  const consumeMutation = useMutation({
    mutationFn: () => consumeBindCode(bindCode.trim(), token!),
    onSuccess: async (data) => {
      setNotice(data.message);
      setBindCode("");
      if (data.access_token) {
        await setToken(data.access_token);
        queryClient.clear();
      } else {
        await Promise.all([
          queryClient.invalidateQueries({ queryKey: ["profile"] }),
          queryClient.invalidateQueries({ queryKey: ["identities"] }),
        ]);
      }
    },
    onError: (error: Error) => setNotice(error.message),
  });

  const generatedCode = createMutation.data;
  const loading = profileQuery.isLoading || identitiesQuery.isLoading;
  const identityRows = useMemo(() => {
    const rows = [...(identitiesQuery.data || [])];
    const currentPlatform = String(profileQuery.data?.platform || "").trim().toLowerCase();
    if (
      currentPlatform &&
      !rows.some((item) => String(item.platform || "").trim().toLowerCase() === currentPlatform)
    ) {
      rows.unshift({
        platform: currentPlatform,
        platform_id: currentPlatform === "ios" ? "当前 iPhone / iPad" : currentPlatform === "android" ? "当前 Android 设备" : "当前客户端",
      });
    }
    return rows;
  }, [identitiesQuery.data, profileQuery.data?.platform]);

  return (
    <PanelModal visible={visible} title="账号绑定" onClose={onClose}>
      <View style={styles.hero}>
        <Text style={styles.heroTitle}>跨端合并账号</Text>
        <Text style={styles.heroDesc}>生成 6 位绑定码，在其他平台输入即可将数据合并到同一账号。</Text>
      </View>

      {notice ? <Text style={styles.notice}>{notice}</Text> : null}

      {loading ? (
        <View style={styles.loadingBox}>
          <ActivityIndicator color={colors.primary} />
          <Text style={styles.loadingText}>正在读取绑定信息...</Text>
        </View>
      ) : null}

      {!loading ? (
        <>
          <View style={styles.card}>
            <Text style={styles.cardTitle}>当前账号</Text>
            <Text style={styles.cardMeta}>{profileQuery.data?.nickname || "用户"}</Text>
            {profileQuery.data?.email ? <Text style={styles.cardSub}>{profileQuery.data.email}</Text> : null}
            {/* binding_stage hidden from app UI */}
          </View>

          <View style={styles.card}>
            <Text style={styles.cardTitle}>已绑定身份</Text>
            {identityRows.length === 0 ? (
              <Text style={styles.cardSub}>当前还没有历史身份记录</Text>
            ) : (
              identityRows.map((item) => (
                <View key={`${item.platform}-${item.platform_id}`} style={styles.identityRow}>
                  <Text style={styles.identityName}>{platformLabel(item.platform)}</Text>
                  <Text style={styles.identityValue}>{platformValue(item.platform, item.platform_id)}</Text>
                </View>
              ))
            )}
          </View>

          <View style={styles.card}>
            <Text style={styles.cardTitle}>生成绑定码</Text>
            <Text style={styles.cardSub}>默认 10 分钟有效，用于在另一端完成数据合并。</Text>
            <Pressable
              style={[styles.primaryBtn, createMutation.isPending && styles.primaryBtnDisabled]}
              onPress={() => void createMutation.mutateAsync()}
              disabled={createMutation.isPending}
            >
              <Text style={styles.primaryBtnText}>{createMutation.isPending ? "生成中..." : "生成 6 位绑定码"}</Text>
            </Pressable>
            {generatedCode ? (
              <View style={styles.codeBox}>
                <Text style={styles.codeLabel}>当前绑定码</Text>
                <Text selectable style={styles.codeText}>
                  {generatedCode.code}
                </Text>
                <Text style={styles.codeHint}>到期时间：{formatMdHmLocal(generatedCode.expires_at)}</Text>
              </View>
            ) : null}
          </View>

          <View style={styles.card}>
            <Text style={styles.cardTitle}>输入绑定码</Text>
            <TextInput
              value={bindCode}
              onChangeText={(value) => {
                setBindCode(value.replace(/\D+/g, "").slice(0, 6));
                setNotice(null);
              }}
              keyboardType="number-pad"
              placeholder="请输入 6 位数字"
              placeholderTextColor={colors.text4}
              style={styles.input}
            />
            <Pressable
              style={[styles.primaryBtn, (!/^\d{6}$/.test(bindCode) || consumeMutation.isPending) && styles.primaryBtnDisabled]}
              onPress={() => void consumeMutation.mutateAsync()}
              disabled={!/^\d{6}$/.test(bindCode) || consumeMutation.isPending}
            >
              <Text style={styles.primaryBtnText}>{consumeMutation.isPending ? "绑定中..." : "确认绑定"}</Text>
            </Pressable>
          </View>
        </>
      ) : null}
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
  notice: {
    borderRadius: radii.md,
    backgroundColor: colors.primaryLight,
    color: colors.primaryDark,
    paddingHorizontal: 14,
    paddingVertical: 12,
    fontSize: 13,
    lineHeight: 19,
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
  cardMeta: {
    fontSize: 16,
    fontWeight: "700",
    color: colors.text2,
  },
  cardSub: {
    fontSize: 13,
    lineHeight: 19,
    color: colors.text3,
  },
  identityRow: {
    gap: 4,
    paddingVertical: 8,
    borderBottomWidth: 1,
    borderBottomColor: colors.borderLight,
  },
  identityName: {
    fontSize: 13,
    fontWeight: "700",
    color: colors.text2,
  },
  identityValue: {
    fontSize: 12,
    color: colors.text3,
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
  codeBox: {
    gap: 6,
    borderRadius: radii.md,
    backgroundColor: colors.bg,
    padding: 14,
  },
  codeLabel: {
    fontSize: 12,
    fontWeight: "700",
    color: colors.text3,
  },
  codeText: {
    fontSize: 30,
    fontWeight: "800",
    letterSpacing: 4,
    color: colors.primaryDark,
  },
  codeHint: {
    fontSize: 12,
    color: colors.text3,
  },
  input: {
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radii.md,
    backgroundColor: colors.bg,
    paddingHorizontal: 14,
    paddingVertical: 14,
    fontSize: 18,
    fontWeight: "700",
    letterSpacing: 4,
    color: colors.text,
  },
});
