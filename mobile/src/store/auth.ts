import { create } from "zustand";
import * as SecureStore from "expo-secure-store";

const TOKEN_KEY = "pai_mobile_token";

interface AuthState {
  ready: boolean;
  token: string | null;
  loadToken: () => Promise<void>;
  setToken: (token: string | null) => Promise<void>;
}

export const useAuthStore = create<AuthState>((set) => ({
  ready: false,
  token: null,
  loadToken: async () => {
    try {
      const token = await SecureStore.getItemAsync(TOKEN_KEY);
      set({
        ready: true,
        token: token || null,
      });
    } catch {
      set({
        ready: true,
        token: null,
      });
    }
  },
  setToken: async (token) => {
    try {
      if (token) {
        await SecureStore.setItemAsync(TOKEN_KEY, token);
      } else {
        await SecureStore.deleteItemAsync(TOKEN_KEY);
      }
    } finally {
      set({ token: token || null });
    }
  },
}));
