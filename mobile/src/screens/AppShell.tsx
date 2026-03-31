import { useCallback, useEffect, useRef, useState } from "react";
import { Keyboard, Platform, StyleSheet, View } from "react-native";
import { useQueryClient } from "@tanstack/react-query";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { colors } from "../design/tokens";
import { getTabBarInset, MiniTabBar, TabKey } from "../components/MiniTabBar";
import { useAuthStore } from "../store/auth";
import { CalendarTab } from "./tabs/CalendarTab";
import { ChatTab } from "./tabs/ChatTab";
import { StatsTab } from "./tabs/HomeTab";
import { LedgerTab } from "./tabs/LedgerTab";
import { MeTab } from "./tabs/MeTab";

export function AppShell() {
  const [tab, setTab] = useState<TabKey>("chat");
  const [keyboardVisible, setKeyboardVisible] = useState(false);
  const setToken = useAuthStore((state) => state.setToken);
  const queryClient = useQueryClient();
  const insets = useSafeAreaInsets();
  const tabBarInset = getTabBarInset(insets.bottom);
  const chatBottomInset = keyboardVisible ? 0 : tabBarInset;
  const showTabBar = !(tab === "chat" && keyboardVisible);

  // Cross-tab prefill support: navigate to chat with pre-filled text
  const chatPrefillRef = useRef<string | undefined>(undefined);

  const navigateToChat = useCallback((prefill?: string) => {
    chatPrefillRef.current = prefill;
    setTab("chat");
  }, []);

  const consumePrefill = useCallback(() => {
    const text = chatPrefillRef.current;
    chatPrefillRef.current = undefined;
    return text;
  }, []);

  useEffect(() => {
    const showEvent = Platform.OS === "ios" ? "keyboardWillShow" : "keyboardDidShow";
    const hideEvent = Platform.OS === "ios" ? "keyboardWillHide" : "keyboardDidHide";
    const showSub = Keyboard.addListener(showEvent, () => setKeyboardVisible(true));
    const hideSub = Keyboard.addListener(hideEvent, () => setKeyboardVisible(false));
    return () => {
      showSub.remove();
      hideSub.remove();
    };
  }, []);

  const handleNavigate = useCallback((target: TabKey, prefill?: string) => {
    if (target === "chat" && prefill) {
      navigateToChat(prefill);
    } else {
      setTab(target);
    }
  }, [navigateToChat]);

  async function handleLogout() {
    await setToken(null);
    queryClient.clear();
  }

  return (
    <View style={styles.container}>
      {tab === "ledger" ? <LedgerTab bottomInset={tabBarInset} /> : null}
      {tab === "calendar" ? <CalendarTab bottomInset={tabBarInset} /> : null}
      {tab === "chat" ? <ChatTab bottomInset={chatBottomInset} consumePrefill={consumePrefill} /> : null}
      {tab === "stats" ? <StatsTab bottomInset={tabBarInset} onNavigate={handleNavigate} /> : null}
      {tab === "me" ? <MeTab bottomInset={tabBarInset} onNavigate={handleNavigate} onLogout={handleLogout} /> : null}
      {showTabBar ? <MiniTabBar currentTab={tab} onChange={setTab} /> : null}
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: colors.bg,
  },
});
