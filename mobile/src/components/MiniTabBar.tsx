import { Ionicons } from "@expo/vector-icons";
import { useEffect, useRef } from "react";
import { Animated, Pressable, StyleSheet, Text, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { colors, radii, shadowLg, shadowMd } from "../design/tokens";

export type TabKey = "ledger" | "calendar" | "chat" | "stats" | "me";

type TabBarProps = {
  currentTab: TabKey;
  hidden?: boolean;
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

export function MiniTabBar({ currentTab, hidden = false, onChange }: TabBarProps) {
  const insets = useSafeAreaInsets();
  const bottomPadding = Math.max(insets.bottom - TAB_BAR_OFFSET_REDUCTION, TAB_BAR_MIN_BOTTOM_PADDING);
  const visibility = useRef(new Animated.Value(hidden ? 0 : 1)).current;

  useEffect(() => {
    Animated.timing(visibility, {
      toValue: hidden ? 0 : 1,
      duration: hidden ? 180 : 240,
      useNativeDriver: true,
    }).start();
  }, [hidden, visibility]);

  const translateY = visibility.interpolate({
    inputRange: [0, 1],
    outputRange: [28, 0],
  });

  return (
    <Animated.View
      pointerEvents={hidden ? "none" : "box-none"}
      style={[
        styles.wrap,
        {
          paddingBottom: bottomPadding,
          opacity: visibility,
          transform: [{ translateY }],
        },
      ]}
    >
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
                <View style={[styles.iconHolder, active && styles.iconHolderActive]}>
                  <Ionicons
                    name={active ? item.activeIcon : item.icon}
                    size={22}
                    color={active ? colors.primary : colors.text4}
                  />
                </View>
              )}
              <Text style={[styles.label, active && styles.labelActive, isChat && active && styles.labelChat]}>
                {item.label}
              </Text>
            </Pressable>
          );
        })}
      </View>
    </Animated.View>
  );
}

const styles = StyleSheet.create({
  wrap: {
    position: "absolute",
    left: 0,
    right: 0,
    bottom: 0,
    paddingHorizontal: 12,
  },
  bar: {
    height: TAB_BAR_HEIGHT,
    flexDirection: "row",
    alignItems: "flex-end",
    justifyContent: "space-around",
    backgroundColor: "rgba(255,255,255,0.96)",
    borderRadius: radii.xl,
    borderWidth: 1,
    borderColor: "rgba(238,240,246,0.8)",
    overflow: "visible",
    ...shadowLg,
  },
  item: {
    flex: 1,
    alignItems: "center",
    justifyContent: "flex-end",
    gap: 3,
    paddingBottom: 10,
  },
  chatItem: {
    paddingBottom: 6,
    zIndex: 10,
  },
  iconHolder: {
    width: 36,
    height: 36,
    borderRadius: 18,
    alignItems: "center",
    justifyContent: "center",
  },
  iconHolderActive: {
    backgroundColor: colors.primaryLight,
  },
  chatIconWrap: {
    width: 54,
    height: 54,
    borderRadius: 27,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: colors.primary,
    transform: [{ translateY: -8 }],
    borderWidth: 3,
    borderColor: "rgba(255,255,255,0.96)",
    ...shadowMd,
  },
  chatIconWrapActive: {
    backgroundColor: colors.primary,
    transform: [{ translateY: -10 }],
  },
  label: {
    fontSize: 11,
    fontWeight: "600",
    color: colors.text4,
  },
  labelActive: {
    color: colors.primary,
    fontWeight: "700",
  },
  labelChat: {
    color: colors.primary,
    fontWeight: "800",
  },
});
