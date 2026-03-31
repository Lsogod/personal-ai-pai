import { Ionicons } from "@expo/vector-icons";
import { Pressable, StyleSheet, Text, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { colors, radii, shadowMd } from "../design/tokens";

export type TabKey = "ledger" | "calendar" | "chat" | "stats" | "me";

type TabBarProps = {
  currentTab: TabKey;
  onChange: (tab: TabKey) => void;
};

const TAB_ITEMS: Array<{
  key: TabKey;
  label: string;
  icon: keyof typeof Ionicons.glyphMap;
  activeIcon: keyof typeof Ionicons.glyphMap;
}> = [
  { key: "ledger", label: "账单", icon: "wallet-outline", activeIcon: "wallet" },
  { key: "calendar", label: "日程", icon: "calendar-outline", activeIcon: "calendar" },
  { key: "chat", label: "助手", icon: "sparkles-outline", activeIcon: "sparkles" },
  { key: "stats", label: "统计", icon: "stats-chart-outline", activeIcon: "stats-chart" },
  { key: "me", label: "我的", icon: "person-outline", activeIcon: "person" },
];

export const TAB_BAR_HEIGHT = 74;
const TAB_BAR_MIN_BOTTOM_PADDING = 4;
const TAB_BAR_OFFSET_REDUCTION = 8;

export function getTabBarInset(bottomInset: number) {
  return TAB_BAR_HEIGHT + Math.max(bottomInset - TAB_BAR_OFFSET_REDUCTION, TAB_BAR_MIN_BOTTOM_PADDING);
}

export function MiniTabBar({ currentTab, onChange }: TabBarProps) {
  const insets = useSafeAreaInsets();
  const bottomPadding = Math.max(insets.bottom - TAB_BAR_OFFSET_REDUCTION, TAB_BAR_MIN_BOTTOM_PADDING);

  return (
    <View pointerEvents="box-none" style={[styles.wrap, { paddingBottom: bottomPadding }]}>
      <View style={styles.bar}>
        {TAB_ITEMS.map((item) => {
          const active = currentTab === item.key;
          const isChat = item.key === "chat";
          return (
            <Pressable
              key={item.key}
              style={[styles.item, isChat && styles.chatItem]}
              onPress={() => onChange(item.key)}
            >
              {isChat ? (
                <View style={[styles.chatIconWrap, active && styles.chatIconWrapActive]}>
                  <Ionicons
                    name={active ? item.activeIcon : item.icon}
                    size={22}
                    color="#fff"
                  />
                </View>
              ) : (
                <Ionicons
                  name={active ? item.activeIcon : item.icon}
                  size={22}
                  color={active ? colors.primary : colors.text3}
                />
              )}
              <Text style={[styles.label, active && styles.labelActive, isChat && active && styles.labelChat]}>
                {item.label}
              </Text>
            </Pressable>
          );
        })}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: {
    position: "absolute",
    left: 0,
    right: 0,
    bottom: 0,
    paddingHorizontal: 14,
  },
  bar: {
    height: TAB_BAR_HEIGHT,
    flexDirection: "row",
    alignItems: "flex-end",
    justifyContent: "space-around",
    backgroundColor: "rgba(255,255,255,0.97)",
    borderRadius: radii.xl,
    borderWidth: 1,
    borderColor: colors.borderLight,
    ...shadowMd,
  },
  item: {
    flex: 1,
    alignItems: "center",
    justifyContent: "flex-end",
    gap: 2,
    paddingBottom: 11,
  },
  chatItem: {
    paddingBottom: 8,
  },
  chatIconWrap: {
    width: 50,
    height: 50,
    borderRadius: 25,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: colors.primary,
    transform: [{ translateY: -2 }],
    ...shadowMd,
  },
  chatIconWrapActive: {
    backgroundColor: colors.primaryDark,
  },
  label: {
    fontSize: 11,
    fontWeight: "600",
    color: colors.text3,
  },
  labelActive: {
    color: colors.primary,
  },
  labelChat: {
    fontWeight: "700",
    color: colors.primaryDark,
  },
});
