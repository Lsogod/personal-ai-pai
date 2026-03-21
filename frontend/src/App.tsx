import { ChatPage } from "./pages/chat/ChatPage";
import { LoginPage } from "./pages/login/LoginPage";
import { AdminPage } from "./pages/admin/AdminPage";
import { useAuthStore } from "./store/auth";
import { ErrorBoundary } from "./components/ErrorBoundary";

export default function App() {
  if (window.location.pathname.startsWith("/admin")) {
    return (
      <ErrorBoundary>
        <AdminPage />
      </ErrorBoundary>
    );
  }
  const { token } = useAuthStore();
  return (
    <ErrorBoundary>
      {token ? <ChatPage /> : <LoginPage />}
    </ErrorBoundary>
  );
}
