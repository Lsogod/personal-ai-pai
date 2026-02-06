import { create } from "zustand";

interface AuthState {
  token: string | null;
  setToken: (token: string | null) => void;
}

export const useAuthStore = create<AuthState>((set) => ({
  token: localStorage.getItem("pai_token"),
  setToken: (token) => {
    if (token) {
      localStorage.setItem("pai_token", token);
    } else {
      localStorage.removeItem("pai_token");
    }
    set({ token });
  }
}));
