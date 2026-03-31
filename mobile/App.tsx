import { useEffect } from "react";
import { ActivityIndicator, StyleSheet, Text } from "react-native";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StatusBar } from "expo-status-bar";
import { SafeAreaProvider, SafeAreaView } from "react-native-safe-area-context";

import { colors } from "./src/design/tokens";
import { AppShell } from "./src/screens/AppShell";
import { LoginScreen } from "./src/screens/LoginScreen";
import { useAuthStore } from "./src/store/auth";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      staleTime: 15_000,
    },
  },
});

function BootScreen() {
  return (
    <SafeAreaView style={styles.bootScreen} edges={["top", "bottom"]}>
      <ActivityIndicator size="large" color="#0f172a" />
      <Text style={styles.bootTitle}>PAI Mobile</Text>
      <Text style={styles.bootHint}>正在读取本地登录状态</Text>
    </SafeAreaView>
  );
}

function RootApp() {
  const ready = useAuthStore((state) => state.ready);
  const token = useAuthStore((state) => state.token);
  const loadToken = useAuthStore((state) => state.loadToken);

  useEffect(() => {
    void loadToken();
  }, [loadToken]);

  if (!ready) {
    return <BootScreen />;
  }

  return token ? <AppShell /> : <LoginScreen />;
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <SafeAreaProvider>
        <StatusBar style="dark" backgroundColor={colors.bg} />
        <RootApp />
      </SafeAreaProvider>
    </QueryClientProvider>
  );
}

const styles = StyleSheet.create({
  bootScreen: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: colors.bg,
    gap: 12,
    paddingHorizontal: 24,
  },
  bootTitle: {
    fontSize: 24,
    fontWeight: "700",
    color: colors.text,
  },
  bootHint: {
    fontSize: 14,
    color: colors.text3,
  },
});
