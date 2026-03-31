import { useState } from "react";
import { StyleSheet, View } from "react-native";
import { useQueryClient } from "@tanstack/react-query";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { colors } from "../design/tokens";
import { getTabBarInset, MiniTabBar, TabKey } from "../components/MiniTabBar";
import { useAuthStore } from "../store/auth";
import { CalendarTab } from "./tabs/CalendarTab";
import { CommandTab } from "./tabs/CommandTab";
import { HomeTab } from "./tabs/HomeTab";
import { LedgerTab } from "./tabs/LedgerTab";
import { MeTab } from "./tabs/MeTab";

export function AppShell() {
  const [tab, setTab] = useState<TabKey>("home");
  const setToken = useAuthStore((state) => state.setToken);
  const queryClient = useQueryClient();
  const insets = useSafeAreaInsets();
  const tabBarInset = getTabBarInset(insets.bottom);

  async function handleLogout() {
    await setToken(null);
    queryClient.clear();
  }

  return (
    <View style={styles.container}>
      {tab === "home" ? <HomeTab bottomInset={tabBarInset} onNavigate={setTab} /> : null}
      {tab === "command" ? <CommandTab bottomInset={tabBarInset} /> : null}
      {tab === "ledger" ? <LedgerTab bottomInset={tabBarInset} /> : null}
      {tab === "calendar" ? <CalendarTab bottomInset={tabBarInset} /> : null}
      {tab === "me" ? <MeTab bottomInset={tabBarInset} onNavigate={setTab} onLogout={handleLogout} /> : null}
      <MiniTabBar currentTab={tab} onChange={setTab} />
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: colors.bg,
  },
});
