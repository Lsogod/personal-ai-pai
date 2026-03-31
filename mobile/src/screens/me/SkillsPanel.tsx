import { ActivityIndicator, Pressable, StyleSheet, Text, View } from "react-native";
import { useQuery } from "@tanstack/react-query";

import { PanelModal } from "../../components/PanelModal";
import { fetchSkills, SkillItem } from "../../lib/api";
import { colors, radii, surfaceCard } from "../../design/tokens";

type SkillsPanelProps = {
  visible: boolean;
  token: string | null;
  onClose: () => void;
};

function getStatusLabel(item: SkillItem) {
  const source = item.source === "builtin" ? "内置" : "用户";
  const statusMap: Record<string, string> = {
    BUILTIN: "内置",
    DRAFT: "草稿",
    PUBLISHED: "已发布",
    DISABLED: "已停用",
  };
  return `${source} · ${statusMap[item.status] || item.status}`;
}

export function SkillsPanel({ visible, token, onClose }: SkillsPanelProps) {
  const skillsQuery = useQuery({
    queryKey: ["skills"],
    enabled: visible && !!token,
    queryFn: () => fetchSkills(token!),
  });

  return (
    <PanelModal visible={visible} title="技能管理" onClose={onClose}>
      <View style={styles.hero}>
        <Text style={styles.heroTitle}>当前账号技能清单</Text>
        <Text style={styles.heroDesc}>查看和管理你的 AI 技能列表。</Text>
      </View>

      <Pressable style={styles.refreshBtn} onPress={() => void skillsQuery.refetch()}>
        <Text style={styles.refreshText}>刷新列表</Text>
      </Pressable>

      {skillsQuery.isLoading ? (
        <View style={styles.loadingBox}>
          <ActivityIndicator color={colors.primary} />
          <Text style={styles.loadingText}>正在加载技能...</Text>
        </View>
      ) : null}

      {!skillsQuery.isLoading && (skillsQuery.data || []).length === 0 ? (
        <View style={styles.emptyBox}>
          <Text style={styles.emptyTitle}>还没有技能</Text>
          <Text style={styles.emptyDesc}>等后面把原生端的创建/发布入口补上，这里会直接显示你的技能草稿和已发布版本。</Text>
        </View>
      ) : null}

      {(skillsQuery.data || []).map((item) => (
        <View key={`${item.source}-${item.slug}`} style={styles.skillCard}>
          <View style={styles.skillHead}>
            <Text style={styles.skillName}>{item.name}</Text>
            <Text style={styles.skillStatus}>{getStatusLabel(item)}</Text>
          </View>
          <Text style={styles.skillDesc}>{item.description || "暂无描述"}</Text>
          <Text style={styles.skillMeta}>slug: {item.slug}</Text>
          <Text style={styles.skillMeta}>版本: v{item.active_version}</Text>
        </View>
      ))}
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
  refreshBtn: {
    alignSelf: "flex-start",
    paddingHorizontal: 14,
    paddingVertical: 10,
    borderRadius: radii.full,
    backgroundColor: colors.primaryLight,
  },
  refreshText: {
    fontSize: 13,
    fontWeight: "700",
    color: colors.primaryDark,
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
  emptyBox: {
    gap: 8,
    padding: 18,
    ...surfaceCard,
  },
  emptyTitle: {
    fontSize: 16,
    fontWeight: "800",
    color: colors.text,
  },
  emptyDesc: {
    fontSize: 13,
    lineHeight: 20,
    color: colors.text3,
  },
  skillCard: {
    gap: 8,
    padding: 18,
    ...surfaceCard,
  },
  skillHead: {
    gap: 6,
  },
  skillName: {
    fontSize: 17,
    fontWeight: "800",
    color: colors.text,
  },
  skillStatus: {
    alignSelf: "flex-start",
    fontSize: 12,
    fontWeight: "700",
    color: colors.primaryDark,
    backgroundColor: colors.primaryLight,
    borderRadius: radii.full,
    paddingHorizontal: 10,
    paddingVertical: 5,
  },
  skillDesc: {
    fontSize: 14,
    lineHeight: 20,
    color: colors.text2,
  },
  skillMeta: {
    fontSize: 12,
    color: colors.text3,
  },
});
